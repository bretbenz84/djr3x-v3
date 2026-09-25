# LED integration

Implemented September 25, 2026; **not activated on hardware**. V2 services continue
running. Head and chest devices are independently optional through `head_led_port`
and `chest_led_port`; empty defaults open neither device and offer no LED tool.
Ports must be distinct explicit macOS `/dev/cu.*` paths. `pyserial==3.5` is the only
LED dependency, already in `requirements-robot.txt`. No extra model is loaded.

## Behavior and ownership

The V3 `LEDs` owner uses the existing 115200-baud newline protocol, with exclusive
opens, bounded writes and a two-second Arduino boot wait. Firmware source was
inspected, not flashed or verified against the installed boards. Firmware owns
pattern rendering. Startup sends chest `STARTUP` and warm gold active eyes before
servo wake. Shutdown sends `FADEOFF` no later than servo park begins, then retains the LED ports
through the four-second fade. The shutdown latch prevents late speech cleanup from
waking the lights again. A fade failure does not skip servo park.

Ordinary speech automatically selects neutral LEDs on actual playback start.
The audio callback supplies only bounded PCM/timestamps to an eight-slot mailbox;
it never writes serial. A single 20 Hz worker computes RMS from DAC-due PCM using
V2's scale 800, sends changes of at least eight brightness steps, and refreshes
levels at least every 500 ms for the firmware's 1.5-second watchdog. Silence/starvation
reduces the level to zero. After completion/interruption, three head `SPEAK_STOP`
commands and `ACTIVE` restore idle mouth glow and eye blinking; the chest returns
to idle. This is approximate playback synchronization, not measured LED latency.
Talking servos remain independently synchronized by the same output owner.

The sole conversation model can use `animate_lights`:

- `during_speech`: choose neutral, happy, excited, sad, angry or curious for the
  accompanying phrase. The pattern starts only when audio starts.
- `before_speech`: show one of those patterns, or chest compliment sparkles, for
  one or 2.5 seconds. Optional text follows; null text produces a silent animation.

Curious has purple eyes/mouth and the chest's neutral fallback. Compliment is offered
only with a chest and is a standalone effect. These are theatrical styles, not
inferences about a human's feelings. The model cannot send raw serial commands,
change ports, operate battery displays or override lifecycle shutdown. No speech
regex selects patterns, and no additional model call is needed for these actions.
Sleep/charge modes, arbitrary RGB controls, calendar Pride policy and V2's automatic
reconnect/heartbeat subsystem were not imported.

CoreState validates the offered capability, pattern, state revision and request;
the output owner rechecks state, expiry and LED generation before execution.
Requests cannot replay across intervening light activity. Serial failures latch the
capability unavailable and attempt to stop healthy boards without reconnecting.
Speech can complete even if its decorative lights fail. Structured `led_delivery`
receipts and conversation history report commands sent, failure, cancellation or
supersession separately from heard speech. Neither a serial write nor firmware's
lack of error establishes that LEDs visibly rendered.

## Prepared cutover

The refreshed, ignored bundle is
`local/operator-migration/20260925T193611891369Z/`. It contains the existing supervisor
and servo jobs plus settings for the inspected head/chest paths, original agent
backups and digests. Default settings and installed jobs were not changed. The
previous servo-only bundle is preserved. The preparation utility now accepts
`--head-led-port` and `--chest-led-port`.

Follow [the cutover guide](OPERATOR_CUTOVER.md) only in a future supervised session.
The existing V2 LED menu and battery menu retain off-program controls and yield to
the shared controller lock. V3 claims both configured LED resource leases and
checks actual descriptor release with `lsof` before opening either board. Failure
or uncertainty aborts handoff. These V2 companions are not imported or launched by
V3; independent V3 menu replacements remain future work. No live cross-version
handoff is claimed from synthetic tests or source inspection.

## Verification and remaining acceptance

`.venv/bin/python tools/run_checks.py`: **548 checks**,
including 20 new LED tests, use fake serial, synthetic PCM/providers and temporary
state under blocked external I/O. Production Session/state/output tests cover
one-generation style selection, silent animations, automatic speaking, unavailable
capability rejection, stale generations, playback/serial failure and cancellation.
Transport tests cover startup/fade ordering, partial startup cleanup, multi-port
handoff, amplitude/keepalive, bounded future-DAC samples and late cleanup. The
existing real AudioSink adapter test now checks its PCM observer against played
bytes through a fake device callback.

Pending live gates: installed firmware/protocol compatibility; actual head/chest
identity; boot/reset timing; V2 LED/battery menu yield and recovery; visual colors,
amplitude, blinking and animation timing; startup/park fade synchronization;
interruption/disconnect behavior; playback underruns and CPU/RSS/median-tail latency
under robot load. No real LED timing, added resident memory, acoustic or hardware
acceptance measurements were made in this slice.
