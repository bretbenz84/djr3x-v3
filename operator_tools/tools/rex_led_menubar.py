#!/usr/bin/env python3
"""
rex_led_menubar.py — macOS menu bar LED animation console for the LED board.

A small always-on menu bar app (rumps/Cocoa, sibling of rex_servo_menubar.py)
titled "LED Control". The dropdown has two areas — Head and Chest — with one
clickable button per animation the firmware supports for that strip (the
commands documented in firmware/djr3x_leds/head.cpp and chest.cpp). Click a
button and the command goes straight down the wire, so you can audition any
animation while the robot is off. No dependency on the conversation core; the
port comes from local/settings.toml through v3_settings.

One ESP32 drives both strips on one serial port (see led_board.py). A head
button sends `H:<command>` and a chest button `C:<command>`.

Head speak animations are an equalizer driven by a SPEAK_LEVEL:{0-255} stream
that the robot normally derives from live TTS audio. Clicking a head Speak button
therefore also starts a synthetic level wave (a few overlapping sines) so the
mouth actually dances; any other head button stops the wave (Speak Stop sends
SPEAK_STOP first). Chest speak patterns animate autonomously — no stream needed.

Battery Meter Mode (the top menu item, DEFAULT ON):
  While Rex is off, the battery menu bar app (tools/rex_battery_menubar.py) is
  what paints the chest's 24-pixel charge gauge and the mouth's SOC breathing —
  it briefly opens the board whenever the level or charger state changes.
  The port is exclusive-open, so this console holding it permanently meant those
  opens silently failed forever and the robot sat dark on the charger (owner
  2026-08-04: "the LED Control app is hijacking control of that board"). So the
  console starts in Battery Meter Mode: port released, buttons inert, gauge
  visible. Toggle the menu item (or just click any animation — that takes the
  port automatically) to switch to LED-control mode; the choice persists in
  local/operator/led_console_mode.json across relaunches.

How it shares the serial port with the robot (the port is exclusive-open):
  Same dormant pattern as the battery meter and servo console. The robot holds
  the single-instance flock for its whole lifetime; the worker polls it ~1×/s:
    - lock held  → close the port (the robot owns the LEDs), buttons inert,
                   status row shows "Rex is running"
    - lock free  → reopen the port and the buttons go live
  After each open the board is asked to identify itself. A click made before it
  has answered (or the one that flips out of Battery Meter Mode) is held in the
  queue and sent as soon as the board is confirmed.

Run directly for debugging:
    .venv/bin/python operator_tools/tools/rex_led_menubar.py
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# utils.single_instance must be importable WITHOUT the conversation core
# (mirrors the battery/servo apps — this process must start even when the
# robot program cannot).
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import led_board  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | rex_led | %(levelname)s | %(message)s",
)
log = logging.getLogger("rex_led")

_LOCK_POLL_SECS = 1.0
_SPEAK_LEVEL_HZ = 30.0         # synthetic level stream rate for head speak demos
_MAX_QUEUED = 8                # clicks held while the board connects / mode flips
_MODE_STATE_PATH = __import__("v3_settings").STATE / "led_console_mode.json"
_PREFIX = {"head": "H:", "chest": "C:"}

# Animation buttons per strip: (menu label, command). Mirrors each strip's
# command() in firmware/djr3x_leds — keep in sync if the firmware gains a mode.
_HEAD_ANIMATIONS: list[tuple[str, str]] = [
    ("Idle (eyes breathe + mouth glow)", "IDLE"),
    ("Active (steady eyes + glow)", "ACTIVE"),
    ("Speak — neutral", "SPEAK:neutral"),
    ("Speak — happy", "SPEAK:happy"),
    ("Speak — excited", "SPEAK:excited"),
    ("Speak — sad", "SPEAK:sad"),
    ("Speak — angry", "SPEAK:angry"),
    ("Speak — curious", "SPEAK:curious"),
    ("Speak — surprised (icy white)", "SPEAK:surprised"),
    ("Speak — proud (deep gold)", "SPEAK:proud"),
    ("Speak — skeptical (yellow-green)", "SPEAK:skeptical"),
    ("Speak — sympathetic (rose)", "SPEAK:sympathetic"),
    ("Speak Stop", "SPEAK_STOP"),
    ("Sleep (red mouth breathing)", "SLEEP"),
    ("Charge meter (demo 50%)", "CHARGE:50"),
    ("Fade Off (~4s power-down)", "FADEOFF"),
    ("Off", "OFF"),
]

_CHEST_ANIMATIONS: list[tuple[str, str]] = [
    ("Startup (short-circuit intro)", "STARTUP"),
    ("Idle (RandomBlocks2)", "IDLE"),
    ("Active (bright RandomBlocks2)", "ACTIVE"),
    ("Speak — neutral", "SPEAK:neutral"),
    ("Speak — happy (gold + confetti)", "SPEAK:happy"),
    ("Speak — excited (racing gold/red)", "SPEAK:excited"),
    ("Speak — sad (slow blue sighs)", "SPEAK:sad"),
    ("Speak — angry (red alert)", "SPEAK:angry"),
    ("Speak — curious (violet, looking over)", "SPEAK:curious"),
    ("Speak — surprised (blue burst)", "SPEAK:surprised"),
    ("Speak — proud (bars full, gold)", "SPEAK:proud"),
    ("Speak — skeptical (panel stops, eyebrow)", "SPEAK:skeptical"),
    ("Speak — sympathetic (rose heartbeat)", "SPEAK:sympathetic"),
    ("Speak Stop", "SPEAK_STOP"),
    ("Compliment flash (white/blue)", "COMPLIMENT"),
    ("Next built-in pattern", "NEXT"),
    ("Sleep (dim red breathing)", "SLEEP"),
    ("Battery meter (demo 50%, charging)", "CHARGE:50:1"),
    ("Fade Off (~4s power-down)", "FADEOFF"),
    ("Off", "OFF"),
]


def _rex_running() -> bool:
    try:
        from utils import single_instance
        return single_instance.is_held_by_other()
    except Exception as exc:
        log.debug("single_instance check failed: %s", exc)
        return False


# ── Ownership mode: battery meter vs LED control ──────────────────────────────
# Which app owns the LED board while Rex is off. ON (the default) means this
# console keeps its hands off so the battery menu bar app can paint the chest
# charge gauge and the mouth's SOC breathing; OFF means we hold the port and
# the animation buttons are live. Persisted so a relaunch (login, KeepAlive
# restart) doesn't silently take the board back.

_mode_lock = threading.Lock()
_battery_mode = True


def _load_battery_mode() -> bool:
    try:
        data = json.loads(_MODE_STATE_PATH.read_text())
        return bool(data["battery_mode"])
    except (OSError, ValueError, KeyError, TypeError):
        return True          # default: leave the board to the battery meter


def _save_battery_mode(value: bool) -> None:
    try:
        _MODE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MODE_STATE_PATH.write_text(json.dumps({"battery_mode": bool(value)}) + "\n")
    except OSError as exc:
        log.warning("Could not persist LED console mode: %s", exc)


def battery_mode() -> bool:
    with _mode_lock:
        return _battery_mode


def set_battery_mode(value: bool, *, persist: bool = True) -> None:
    global _battery_mode
    value = bool(value)
    with _mode_lock:
        if value == _battery_mode:
            return
        _battery_mode = value
    log.info("Battery Meter Mode %s — %s.", "ON" if value else "OFF",
             "releasing the LED board to the battery meter app" if value
             else "LED Control is taking the port")
    if persist:
        _save_battery_mode(value)


def _status_line(mode: str, detail: str) -> str:
    return {
        "live": f"● {detail}",
        "dormant": "⏸  Rex is running — buttons inert",
        "battery": "🔋 battery meter owns the board",
        "connecting": f"… {detail}",
        "no_port": f"✕ {detail}",
    }.get(mode, detail)


# ── Serial worker ──────────────────────────────────────────────────────────────
# One worker thread owns the board. Buttons enqueue a (strip, command); the
# worker drops to dormant while the robot runs and reconnects on failure. It
# also streams synthetic SPEAK_LEVEL values to the head while a head speak
# animation is active (see module docstring).

class _Board:
    def __init__(self):
        self._lock = threading.Lock()
        self._queue: list[tuple[str, str]] = []
        self._mode = "connecting"       # connecting | live | dormant | battery | no_port
        self._detail = "starting…"
        self._speaking_since: float | None = None

    # UI thread → worker
    def enqueue(self, strip: str, cmd: str) -> None:
        """Queue a command for the worker.

        Dropped only when the board is genuinely unreachable — Rex owns the port,
        or none is configured. Anything else (still connecting, or the pass that
        follows a Battery-Meter-Mode → LED-control flip) HOLDS the click and sends
        it once the board has identified itself, so a button never feels dead.
        """
        with self._lock:
            if self._mode in ("dormant", "no_port"):
                return
            self._queue.append((strip, cmd))
            del self._queue[:-_MAX_QUEUED]

    def status(self) -> tuple[str, str]:
        with self._lock:
            return self._mode, self._detail

    def _set(self, mode: str, detail: str) -> None:
        with self._lock:
            self._mode = mode
            self._detail = detail

    def _synth_level(self, now: float) -> int:
        """A lively fake voice envelope: overlapping sines, clamped to 0–255."""
        t = now - (self._speaking_since or now)
        v = (0.55
             + 0.30 * math.sin(2 * math.pi * 2.1 * t)
             + 0.25 * math.sin(2 * math.pi * 4.7 * t + 1.3)
             + 0.15 * math.sin(2 * math.pi * 0.5 * t))
        return max(0, min(255, int(v * 255)))

    def worker(self, stop: threading.Event) -> None:
        ser = None

        def _close():
            nonlocal ser
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
            with self._lock:
                self._queue.clear()
                self._speaking_since = None

        while not stop.is_set():
            port = led_board.port()
            if not port:
                _close()
                self._set("no_port", "led_port not set in local/settings.toml")
                stop.wait(5.0)
                continue

            if _rex_running():
                if ser is not None:
                    _close()
                    log.info("Rex is running — port released (dormant).")
                self._set("dormant", "Rex is running")
                stop.wait(_LOCK_POLL_SECS)
                continue

            if battery_mode():
                # Hands off: the battery menu bar app needs to open the board to
                # repaint the charge gauge / mouth glow, and it can't while we
                # hold the port. Drop queued clicks too — one that raced the
                # toggle back to battery mode must not fire on some later
                # takeover. ser is None after the close, so the log line happens
                # once per handover, not once a second.
                with self._lock:
                    self._queue.clear()
                if ser is not None:
                    _close()
                    log.info("Battery Meter Mode — port released to the battery meter app.")
                self._set("battery", "battery meter owns the board")
                stop.wait(_LOCK_POLL_SECS)
                continue

            if ser is None:
                self._set("connecting", f"waiting for the LED board on {port}")
                try:
                    ser = led_board.connect(port)
                except Exception as exc:
                    log.debug("open %s failed: %s", port, exc)
                    stop.wait(2.0)
                    continue
                log.info("LED board connected on %s.", port)
            self._set("live", f"live on {port}")

            with self._lock:
                pending = self._queue[:]
                self._queue.clear()
            try:
                for strip, cmd in pending:
                    if strip == "head":
                        # Track speak state so the level wave starts/stops with
                        # the command that changes it. Speak Stop and every
                        # non-speak mode both end the stream.
                        with self._lock:
                            self._speaking_since = (time.monotonic()
                                                    if cmd.startswith("SPEAK:") else None)
                    ser.write((_PREFIX[strip] + cmd + "\n").encode())
                    ser.flush()
                    log.info("%s: sent %s", strip, cmd)
                with self._lock:
                    speaking = self._speaking_since is not None
                if speaking:
                    ser.write(f"H:SPEAK_LEVEL:{self._synth_level(time.monotonic())}\n".encode())
            except Exception as exc:
                log.info("write failed (%s) — reopening.", exc)
                _close()
                continue

            stop.wait(1.0 / _SPEAK_LEVEL_HZ if speaking else 0.1)

        _close()


# ── Menu bar app ───────────────────────────────────────────────────────────────

def run_app() -> int:
    try:
        import rumps
    except ImportError:
        log.error("rumps not installed — run: .venv/bin/pip install rumps")
        return 1

    board = _Board()
    animations = {"head": _HEAD_ANIMATIONS, "chest": _CHEST_ANIMATIONS}
    stop = threading.Event()

    class RexLedApp(rumps.App):
        def __init__(self):
            super().__init__("R3XLed", title="💡 LED Control",
                             quit_button="Quit LED Control")
            # Checked = the board is handed to the battery meter app (see the
            # module docstring). Sits at the top because it decides whether any
            # of the buttons below it do anything.
            self._mode_item = rumps.MenuItem("🔋 Battery Meter Mode",
                                             callback=self._on_toggle_mode)
            self._mode_hint = rumps.MenuItem("hint", callback=lambda _: None)
            self._status = rumps.MenuItem("status", callback=lambda _: None)
            menu: list = [self._mode_item, self._mode_hint, self._status, None]
            for strip in ("head", "chest"):
                menu.append(rumps.MenuItem(f"— {strip.upper()} —", callback=lambda _: None))
                for label, cmd in animations[strip]:
                    menu.append(rumps.MenuItem(label, callback=self._make_cb(strip, cmd)))
                menu.append(None)
            self.menu = menu
            self._timer = rumps.Timer(self._refresh, 1.0)
            self._timer.start()
            # Keep refreshing while the dropdown is open (see the battery meter
            # for the run-loop-mode story); fall back gracefully if rumps changes.
            try:
                from AppKit import NSEventTrackingRunLoopMode
                from Foundation import NSRunLoop
                NSRunLoop.currentRunLoop().addTimer_forMode_(
                    self._timer._nstimer, NSEventTrackingRunLoopMode)
            except Exception as exc:
                log.warning("Could not enable open-menu live updates: %s", exc)
            self._refresh(None)

        def _make_cb(self, strip: str, cmd: str):
            def _cb(_item):
                # Clicking an animation IS a request to take the board — flipping
                # the toggle first would be a pointless extra step, and a silently
                # dead button is worse. The command waits in the queue until the
                # board has identified itself, so it plays a moment later.
                if battery_mode():
                    log.info("Animation clicked (%s %s) — leaving Battery Meter Mode.", strip, cmd)
                    set_battery_mode(False)
                    self._sync_mode_item()
                board.enqueue(strip, cmd)
            return _cb

        def _on_toggle_mode(self, _item):
            set_battery_mode(not battery_mode())
            self._sync_mode_item()

        def _sync_mode_item(self):
            on = battery_mode()
            self._mode_item.state = 1 if on else 0
            self._mode_hint.title = ("   charge gauge is showing — click any "
                                     "animation to take over"
                                     if on else
                                     "   LED Control owns the board — no charge gauge")

        def _refresh(self, _timer):
            self._sync_mode_item()
            self._status.title = _status_line(*board.status())

    # Restore the operator's last choice before the worker can grab the port.
    set_battery_mode(_load_battery_mode(), persist=False)

    threading.Thread(target=board.worker, args=(stop,), daemon=True, name="rex-led-board").start()
    log.info("LED Control menu bar app online (port=%s, battery mode=%s).",
             led_board.port() or "<unset>", "ON" if battery_mode() else "off")
    try:
        RexLedApp().run()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(run_app())
