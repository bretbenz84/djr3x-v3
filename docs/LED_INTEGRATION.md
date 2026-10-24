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
- `before_speech`: show one of those patterns, or `sparkle` (the chest firmware's
  COMPLIMENT effect), for one or 2.5 seconds. Text follows; null text produces a silent
  animation.

Curious has purple eyes/mouth and the chest's neutral fallback. Sparkle is offered only with a
chest, is a standalone effect, and since October 22 touches only the chest: it no longer turns
the eyes green or runs the mouth (run 1072c10a: "he made your mouth sparkle too"; named
`compliment`, the model told the owner it could not make the chest sparkle). When answering a
person, a light request without words is asked once more for the line said after the lights; if
the line still does not come, the lights run on their own. A silent beat of his own may stay silent.
With words, the sparkle runs while he speaks, the mouth talking as usual (run 0421a222: before
speech it added 2.5 s to the reply). These are theatrical styles, not
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

September 27: the LED and battery menus now run as V3 copies; see [operator services](OPERATOR_CUTOVER.md). Earlier cutover notes: The updated
V2 wake supervisor now yields its microphone automatically to manual V3 runs; it
needs only the one-time update/restart, not per-run unloading.
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

## September 25: requested eye blinks

The existing `animate_lights` tool now offers `blink` with a configured head board,
only `before_speech`. It sends three 150 ms dark / 200 ms restored-color eye cycles
through the LED owner, without changing chest or mouth modes. It restores the last
host-requested eye color after interruption; shutdown/version changes prevent late
restoration. Uses existing `EYE:` firmware commands, with no firmware update.
Included in the 573 passing blocked-I/O checks; installed-board rendering and live
language selection remain unverified. Receipts report commands, not visible success.

## September 27: animation request repair

The logged fancy-animation request selected excited/during_speech with empty text,
which failed validation as InvalidToolText before LED execution. Optional empty
animation text now becomes null; silent requests and standalone compliment/blink
patterns execute before speech. The trace preserves the original proposal and
records the prepared request. The model is explicitly directed to use compliment
chest sparkles for a generic animation/sparkle request. Unknown patterns and
unavailable capabilities remain rejected. No regex interprets human speech.

The 597 blocked-I/O checks include the logged argument combination, silent
execution without TTS or another model call, and chest COMPLIMENT execution.
Live model selection and visible rendering remain unverified by these tests.

## October 3: mouth animation missed some lines

The owner reported the mouth did not always animate when Rex spoke. In run `30ec1b04`
every one of the 29 spoken phrases has a `led_delivery` row with `commands_sent`, so the
host believed it had started the mouth each time. V2's head firmware (`head_nano.ino` at
`fb96f08`) documents that the board drops serial bytes while it repaints its LEDs, which
is why `SPEAK_STOP` is sent three times. `SPEAK:<emotion>` was sent once per phrase,
straight after an `EYE:` command that triggers a repaint; when it was lost the firmware
never entered its speaking mode and ignored the levels that followed.

`LEDs.tick` now repeats `SPEAK:<emotion>` 0.12 s and 0.24 s after the start and then once
a second while the phrase plays, each followed at once by the current level (the firmware
restarts the level at zero on `SPEAK:`). A lost start is recovered within a fraction of a
second instead of costing the whole line. The cause is inferred from the firmware source
and the trace, not observed on the board; confirm on the robot that every line animates
and that the once-a-second repeat is not visible as a flicker.

## October 22: the boot line lights the mouth

The please-wait line during startup played straight to the sink and never began the speaking
LEDs (only the ready line logged `led_delivery`). `StartupAudio` now clears the level mailbox,
begins `neutral` speaking lights, plays the line and finishes them; failures are logged as
`startup_audio` `speaking_lights_failed` and never stop the line. Startup sound is written up to
about 1.5 s ahead through PortAudio's buffered stream, so playback levels arrive early with
their predicted play times; the mailbox holds 48 samples (was 8), still bounded. Synthetic
checks only; the boot-line mouth has not been seen on hardware.

## October 23: eyes resent between lines

In run 1e626e07 the eyes went dark for several seconds between lines; no command had been sent
and the Mac's log shows no USB reset of the head board, and they came back with his next line.
The cause (an LED repaint or power dip) is not established. Between lines the eye colour is now
resent every 3 s (`EYE_REFRESH_SECONDS`), never over a requested blink, so such a dropout heals
within seconds. Fake ports only; not yet run on the robot.
