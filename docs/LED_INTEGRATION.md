# LED integration

**Current, November 14, 2026:** one ESP32 (`firmware/djr3x_leds`) drives the head and chest
strips on one serial port, `led_port`, an explicit macOS `/dev/cu.*` path distinct from the
other ports. Empty opens nothing and offers no LED tool. `pyserial==3.5` is the only LED
dependency, in `requirements-robot.txt`. No extra model is loaded. The first two sections
describe what runs now. Sections dated before November 14 are history from the two-board
system; their repeated commands, boot waits and per-board options are gone
([what changed](#november-14-one-led-board)). The chest blends between its states since the
same day ([chest transitions](#november-14-chest-transitions)).

## Behavior and ownership

The V3 `LEDs` owner speaks the board's 115200-baud newline protocol with an exclusive
open and bounded writes. Every command names its strip: `H:` head, `C:` chest. At start it
asks the board to identify itself (`HELLO`) and sends no light command to anything that does
not answer as `djr3x_leds`; the reported firmware version is traced. Firmware owns
pattern rendering. Startup sends chest `STARTUP` and warm gold active eyes before
servo wake. Shutdown sends `FADEOFF` no later than servo park begins, then retains the LED ports
through the four-second fade; with the drive base's battery reading the chest's is
`FADEOFF:<soc>:<0|1>`, which ends in the charge meter (November 15). The shutdown latch prevents late speech cleanup from
waking the lights again. A fade failure does not skip servo park.

Ordinary speech automatically selects neutral LEDs on actual playback start.
The audio callback supplies only bounded PCM/timestamps to an eight-slot mailbox;
it never writes serial. A single 20 Hz worker computes RMS from DAC-due PCM using
V2's scale 800, sends changes of at least eight brightness steps, and refreshes
levels at least every 500 ms for the firmware's 1.5-second watchdog. Silence/starvation
reduces the level to zero. After completion/interruption, head `SPEAK_STOP`
and `ACTIVE` restore idle mouth glow and eye blinking; the chest returns
to idle. This is approximate playback synchronization, not measured LED latency.
Talking servos remain independently synchronized by the same output owner.

The sole conversation model can use `animate_lights`:

- `during_speech`: choose neutral, happy, excited, sad, angry, curious, surprised, proud,
  skeptical or sympathetic for the accompanying phrase. The pattern starts only when audio starts.
- `before_speech`: show one of those patterns, or `sparkle` (the chest firmware's
  COMPLIMENT effect), for one or 2.5 seconds. Text follows; null text produces a silent
  animation.

Since November 15 a line that carries a feeling takes that feeling's lights by itself, with no
light request ([feelings on the lights](#november-15-feelings-on-the-lights)), and every pattern
has a chest pattern of its own (curious used the neutral panel). Sparkle is offered only with a
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
reconnect/heartbeat subsystem were not imported. Since October 25 pride mode renews the
firmware's `PRIDE:1` rainbow mouth and chest every 3 s (the boards drop it after 10 s) and
sends `PRIDE:0` once when the mode ends; sentry mode's red takes precedence. Not yet seen on
the boards.

CoreState validates the offered capability, pattern, state revision and request;
the output owner rechecks state, expiry and LED generation before execution.
Requests cannot replay across intervening light activity. A serial failure latches the
capability unavailable and attempts one stop of both strips, without reconnecting.
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

Superseded November 14: the ESP32 board does not lose bytes while repainting, and the repeats
described here were removed.

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

November 20: the please-wait line is retired ([startup](STARTUP_HEALTH.md)); the ready line still
lights the mouth like any other line.

## October 23: eyes resent between lines

In run 1e626e07 the eyes went dark for several seconds between lines; no command had been sent
and the Mac's log shows no USB reset of the head board, and they came back with his next line.
The cause (an LED repaint or power dip) is not established. Between lines the eye colour is now
resent every 3 s (`EYE_REFRESH_SECONDS`), never over a requested blink, so such a dropout heals
within seconds. Fake ports only; not yet run on the robot.

## November 14: one LED board

Owner request: replace the two Arduino Nanos with one ESP32 and remove the Nano code from V3.
`firmware/djr3x_leds` ports both installed sketches behind one serial port
([wiring and protocol](../firmware/djr3x_leds/README.md)).

Changed in V3:

- **Settings.** `led_port` replaces `head_led_port` and `chest_led_port`. A settings file that
  still has the old names is refused with a message naming `led_port`.
- **LED owner.** One port; `H:`/`C:` prefixes; the identity check replaces the two-second boot
  wait. `SPEAK:` is sent once per phrase and `SPEAK_STOP` once (the October 3 repeats and V2's
  triple stop existed because the Nano lost serial bytes while repainting). The level refresh
  every 500 ms stays, for the firmware's mouth watchdog, and so does the three-second eye
  refresh, whose cause was never established. Sparkle and blink are always offered with the
  board; there is no head-only or chest-only setup.
- **Handoff.** One `leds` resource lease and one port checked with `lsof`, in place of two.
- **Menus.** The LED menu and the battery menu's charge display open the one board through
  `operator_tools/led_board.py`, which makes the same identity check; the battery menu's
  two-second waits and doubled chest command are gone.
- **Dashboard.** One "LED Board" status in place of "Head LEDs" and "Chest LEDs".

Evidence: on a spare board on the bench, the owner watched every head mode, driven at 3.3 V
with no level shifter and each command sent once, and all looked correct
([details](../V2_REFERENCE.md)). The rewritten owner, handoff, menus' settings and dashboard are
checked with a fake board only; the board had been unplugged before the rewritten owner could be
run against it.

Not yet run: this program on the robot; pyserial opening the board (whether the open restarts
it; the identity check allows for either); the whole chest strip; the head line with servos
moving; real speech levels; the LED and battery menus on the board.

To put it on the robot: wire head data to GPIO 18, chest data to GPIO 19 and a ground to the LED
supply; move the chest panels' 5 V to the buck rail; in `local/settings.toml` delete
`head_led_port` and `chest_led_port` and set `led_port`; restart the `com.djr3x.led` and
`com.djr3x.battery` agents so they load the new code.

## November 14: chest transitions

Owner request: the chest went dark and then sparkled when he woke from the charge meter, and
jumped from the startup sparkle to the ordinary panel. LED firmware 0.2.0 changes only the chest
(`firmware/djr3x_leds/chest.cpp`); the commands the program and the menus send are the same.

- **Every change of chest state is blended** from what was showing: 0.2 s into a speaking
  pattern or the sparkle, 0.6 s back to the ordinary panel, 1 s into sleep. The shutdown fade
  is still four seconds; `OFF` alone is instant.
- **Waking from the charge meter.** The meter brightens for 0.3 s, a white spark climbs it from
  the bottom and burns it up (about 0.9 s for a full meter), and the startup sparkle begins when
  the spark reaches the top of the charge. From dark, the sparkle starts at once as before.
- **Startup into the ordinary panel.** During the last three seconds of the sparkle the three
  bars and nine blocks come on one at a time in a random order, each catching with a short
  stutter, under the last sparks. The whole startup is still about 13 s.
- **Speech during startup.** The please-wait line asks for the neutral speaking lights, which
  used to end the sparkle on the spot. Neutral speech, idle and active now wait for the startup
  to finish; an emotion or the sparkle effect still takes over at once.
- **No stale colour.** Coming back to the ordinary panel wipes the previous pattern, so angry's
  red or the sparkle's blue no longer lingers on pixels the panel does not redraw, and the
  brightness no longer drops in one step.

The head is unchanged; its mouth glow already fades in over four seconds.

Evidence: `tools/run_firmware_checks.py` runs the real chest code on the Mac against a stand-in
LED library with a fake clock and reads every frame it would send (10 checks; all 10 fail on
the previous firmware). That shows what is sent, not how it looks. The firmware compiles for the
ESP32 and was flashed to the board on `led_port`, which answered `hello djr3x_leds 0.2.0`; no
light command was sent after flashing.

Not yet seen: any of it on the strip. To judge on the robot: the spark's speed up the meter, the
stutter as the panel comes on (it should read as power catching, not a fault), and the 0.2 s
blend at the start of an excited or angry line. If opening the port restarts the board
(unconfirmed for pyserial), the meter is already dark when startup arrives and the spark cannot
run. After a flash the chest stays dark until the battery menu next repaints the meter (on a
change of charge, or within 30 minutes).

## November 15: the shutdown fade ends in the charge meter

Owner request: link the chest's shutdown fade to the battery meter. Before, the four-second fade
ended dark, and the battery menu painted the meter about 1.5 s later (shutdowns at 1:21 and
1:51 pm the same day), with its own 0.4 s blend and 2 s sweep up from black.

- **Rex sends the level.** At shutdown the LED owner asks the drive base for its last battery
  reading (`DriveBase.charge_reading`: a whole percent, and charging when the controller's charger
  flag is set or the pack is at full-charge voltage, as the battery menu decides them; none if
  unknown or over 30 s old). With one, the chest gets `FADEOFF:<soc>:<0|1>`; without, plain
  `FADEOFF`. The head's fade is unchanged.
- **The meter rises out of the fade** (LED firmware 0.3.0). The panel fades for four seconds as
  before; during the last two the meter sweeps up from the bottom under it, so the meter stands
  where the panel went dark. The blinking top light and the charging dot start as the fade ends.
- **The battery menu confirms it.** Its `CHARGE` a few seconds later changes nothing visible
  (unless the level moved). With no `CHARGE` within a minute (Battery Meter Mode off, or no
  battery app), the meter fades out, so a level nothing updates never stays up.
- **No gold blips under the meter.** The two small chest lights flickered gold for one frame every
  few seconds while the meter showed (RandomEyes ran under it); they now stay dark.

Evidence: the real chest code on the Mac against the stand-in LED library (14 checks, 4 new):
the first two seconds match the plain fade frame for frame, the meter sweeps bottom to top and is
complete at four seconds, no frame is dark, a later `CHARGE` causes no jump, an unconfirmed meter
fades out after a minute, and the chest lights stay dark under the meter. LED owner checks with a
fake board cover the command, the plain fade without a usable reading, and that a failed meter
fade wakes no lights. Flashed to the board on `led_port`, which answered `hello djr3x_leds
0.3.0`; the battery menu then repainted the meter.

Not yet seen: the hand-over on the strip at a real shutdown. Firmware before 0.3.0 ignores
`FADEOFF:<soc>:<0|1>`, so the chest would not fade at all; flash 0.3.0 with this program.

## November 15: the charging mouth is a rainbow meter

Owner request: while he is off and on the charger the mouth breathed in one colour for the
charge; make it like the chest's meter, a rainbow from red at the bottom to blue at the top that
pulses upward. LED firmware 0.4.0, head only ([what it shows](../firmware/djr3x_leds/README.md#mouth-charge-meter)).

- **A meter.** The mouth's ten rows light from the bottom up to the charge, one row for each
  10 %; each row's colour belongs to its place (red, orange, yellow, green, blue), so the level is
  read by where the colours stop, as on the chest.
- **The pulse.** A soft band of light climbs from the bottom row to the top in 2 s, rests 1 s and
  repeats. Above the charge it shows faintly in the empty rows' own colours.
- **Battery menu.** It sent the mouth a new level only when one of five colour bands changed; it
  now sends one whenever the row count changes. It still shows the mouth only while charging.
- **Dashboard.** The mouth mirror draws the same meter for a charge command.

Evidence: the real head code on the Mac against the stand-in LED library (6 new checks): a full
charge shows ten different row colours running red to blue, rows light to the charge, the band's
brightest moment comes later the higher the row and nothing is sent while it rests, empty rows
show faintly, a new level does not blank the meter, and the eyes stay dark. A fake-board check
covers the battery menu's resend, and a mirror check the dashboard (numpy; skipped under the
contract runner, passes run directly). Flashed to the board on `led_port`, which answered
`hello djr3x_leds 0.4.0`.

Not yet seen: any of it on the mouth; he was not on the charger when it was flashed. Which end of
the grid is physically the top is taken from the dashboard's model of the head, not the PCB; one
constant (`MOUTH_ROW0_AT_TOP`) turns it over.

## November 15: feelings on the lights

Owner request: "make sure there are emotional reaction animations for the chest LEDs and eyes and
mouth ... if we make him mad by insulting him his eyes could turn red. If he gets excited or
surprised the chest LEDs should transition to an excitement/surprise animation", integrated with
the feelings his body shows ([servo side](SERVO_INTEGRATION.md#november-15-feelings-on-his-body)).

- **One feeling, body and lights.** The expression the model chooses for a line
  (`respond_with_gesture`, or a save's `expression`) also picks the lights
  (`leds.FEELING_LIGHTS`): angry → red, surprised → white, proud → gold, deflated → sad blue,
  sympathetic → rose, skeptical → yellow-green, tilt and perplexed → curious purple, wiggle →
  excited, laugh → happy. A nod, a lift and an arm emphasis keep the ordinary speaking lights. No
  second model choice and no extra generation; `animate_lights` is still there for a light effect
  asked for by itself, and offers the four new patterns.
- **When.** A line that starts with its expression is spoken in the feeling: the eyes take its
  colour, the mouth talks in it and the chest runs its pattern, then the chest returns to idle. An
  expression that arrives beside a line already being said turns that line's lights
  (`LEDs.feel`), and the rest of the reply keeps the feeling. With no line under it (after the
  line, or before one) the eyes and the mouth's idle glow take the colour and the chest runs the
  pattern while the move lasts, the mouth not moving (`LEDs.react`). When the reply is over the
  eyes and the mouth's glow go back to a plain line's colours (`LEDs.settle`; see below). Sentry
  mode's red is never overridden.
- **A new feeling: angry.** There was no anger among the expressions, so an insult could only get
  a deadpan look. `angry` is a glare (head down 0.2, visor narrowed, held 1.1 s; no pole move) with
  red eyes and mouth and the chest's red alert.
- **LED firmware 0.5.0.** Four more mouth colours (surprised, proud, skeptical, sympathetic) and
  five new chest patterns (those four and curious, which used the neutral panel)
  ([table](../firmware/djr3x_leds/README.md#feelings)).
- **Menus and dashboard.** The LED menu has a button for each new feeling on each strip; the
  dashboard mirror knows their colours (not their motion).
- **`tools/show_light_feelings.py --live`** plays every feeling through the production LED owner,
  naming each as it runs.

Evidence:
- Frames of the real chest and head code on the Mac (15 new checks, 98 firmware checks in all):
  the surprise opens from the middle of each bar within a quarter second, is never a white panel
  and settles; pride fills from the bottom and then nothing blinks; skeptical is three flat lines,
  two blocks and one eyebrow; sympathy beats twice and rests; curious lights one block at a time;
  each leaves no colour behind on the idle panel; each mouth colour is distinct.
- Fake-board checks of the output owner (6 new): the commands for a line with a feeling, a plain
  line, a feeling arriving mid-line, one with no line, and sentry mode.
- Paid probe (`tools/check_gestures.py --live`, real Claude Sonnet 5.5 replies, simulated body, made-up
  lines, 10 replies): being proved right → proud, new wheels → surprised, a dog's death →
  sympathetic, a roast → laugh (happy lights), "worst robot I have ever met" → skeptical, a sum and
  small talk → plain. "Shut up, you worthless pile of scrap" got skeptical under the first wording
  and angry after it said plainly when he is mad (1 of 1; one reply each, one person, no history).
- Flashed; the board answers `hello djr3x_leds 0.5.0`. The first upload stopped part-way ("the chip
  stopped responding") just after the menus released the port; the second verified. The show tool
  then ran once on the robot's lights (3:10 pm).

Not yet seen or judged by the owner: any of the ten on the lights, the glare, and how often he
gets angry over a real evening.

**Later the same day: the eyes let go of the feeling.** A fake-board run showed the eyes after an
angry line staying red, re-sent every 3 s, until his next line; the owner chose to have them reset.
When a reply that showed a feeling ends, the eyes and the mouth's idle glow go back to the colours
a plain line leaves them in; the chest was already idle. A plain reply sends nothing extra, and
sentry mode's red stays. Fake-board checks only (one new, three extended); **not yet seen on the robot**.
Explicit light requests (`animate_lights`, the `lights_` steps) still leave their colour on the eyes
until the next line, as before.

## November 15 (evening): the board drops off, and the mouth does not talk

Run `7261d8ce`. The owner: "the mouth is not animating speech anymore."

- **What the log shows.** Every line has a `led_delivery` row with `commands_sent`, so the host started
  the speaking mouth and fed it levels as before; the host's level path and the head firmware's speaking
  code are unchanged since the single board (November 14) apart from four new colours, and both pass
  their checks with levels. Eleven minutes in, a write to the board failed and every light was dead for
  the rest of the run (`LEDUnavailable` on each line). The run before (`de09e0b3`) failed the same way
  30 s after startup. Neither failure came while he was speaking or moving. The cause of the mouth not
  moving is **not established**; that the board twice stopped taking writes points at its USB link or
  power more than at the program.
- **The board is watched.** The level worker reads what the board says. It speaks only when it boots
  ("ready djr3x_leds ..."), so a reboot mid-run is now logged (`led_board_restarted`) and the lights are
  told what to show again: eyes, idle glow and, during a line, `SPEAK:` so the mouth follows the levels.
- **Reconnect.** A failed write no longer leaves the lights dead: the worker reopens the port every 2 s,
  up to five times, checks the board's name and repaints (`led_reconnect`, then `led_reconnect_failed`
  if it gives up). Lines starting meanwhile are refused at once rather than waiting on the board.
- **Seeing whether the mouth can talk.** `tools/show_light_feelings.py --live --talk` (Rex off) drives
  each feeling's mouth from made-up speech-like loudness through the same level path playback uses. Bars
  rising and falling there but not in conversation would put the fault in playback's levels; still bars
  there put it in the board, its wiring or its power.

Checks: four fake-board checks (a mid-line reboot is told to speak again, other bytes change nothing,
a failed write reopens and repaints, reopening gives up after five tries and shutdown still completes);
the 34 LED owner checks pass. **Not yet run on the robot**, and the mouth itself not yet looked at.

## November 17: a 5 V level shifter on the data lines

The two data lines left the ESP32 at 3.3 V, below what WS2812B and WS2811 strips are specified to
read reliably at 5 V. The owner put a 74AHCT125N between the board and the strips: GPIO 18 into
pin 2 with the head data from pin 3, GPIO 19 into pin 5 with the chest data from pin 6, the chip
powered from the 5 V buck rail, one ground for everything (full pin list in the
[firmware README](../firmware/djr3x_leds/README.md#wiring)). No firmware or program change.

Evidence: the owner's report only: "The lights with the 74AHCT125N no longer flicker at all.
Everything is good." No log or run was read for it and no check was run.

Still open: whether the mouth now follows speech (November 15 evening) and whether the board still
drops off USB mid-run; a level shifter does not explain the second.

## November 21: startup lights paced to his real start

Owner: "The new startup routine needs a new startup LED routine. I like the chest LED startup, but it
would be cool if the 3 columns of 8 LEDs would slowly rise and struggle to stay on. It would also be
cool if the mouth started out flickering the normal yellow light until it were fully illuminated. Try
to make it line up with the actual startup time."

LED firmware 0.6.0 ([what each strip does](../firmware/djr3x_leds/README.md#startup)). The sparks and the
blocks' stuttering arrival are kept. New: the three bars rise from the bottom and struggle under the
sparks, the mouth flickers on in its ordinary amber, and both follow the host's clock instead of a fixed
13 s.

- **Lining up.** `LEDs.start` sends `STARTUP:13200` to both strips (`leds.LIVE_STARTUP_SECONDS`: he is
  ready when everything has loaded and the startup sound's build has played out, about 13.2 s after the
  lights start; everything loaded took 13.6 s in the wake-word run `b28d62e8`, about a second less
  expected since the camera-side models load during the servo wake). Then `App` calls `LEDs.ready`, as
  the startup sound's finish begins, which sends `READY`: the bars lock full and the mouth comes fully on over 0.9 s, the
  time the visor takes to go up, and the ready line follows. A start that runs long keeps the bars
  struggling at the top until `READY`; one that runs short locks them early.
- **Other starts.** A telepresence call and the light demo give no time and send no `READY`: the
  startup lights time themselves (12.8 s), as the LED menu's Startup button does.
- **The head's start.** `H:STARTUP` replaces `H:ACTIVE` at start; the eyes are set as before.
- **Dashboard.** The mirror shows the chest in startup until `READY`.

Evidence: `tools/run_firmware_checks.py` runs both strips' real code against the stand-in library (108
checks; 14 new for the rise, the mouth, the lock, a start that times itself and a host that never says
ready); 2 new blocked-I/O checks for the owner and the mirror. Compiled for the ESP32 (419,452 bytes) and
flashed to the board on `led_port`, which answers `hello djr3x_leds 0.6.0`. Shown to the owner once with
lights only, through the production LED owner (then with 12.8 s): the paced start, `READY`, two seconds of
talking mouth, the ordinary panel, the fade. **Not yet seen in a real start**, where the servo wake and
the ready line run with it; whether 13.2 s matches what a start now takes will show in the next
`startup_timing` row. The look (how hard the bars struggle, how bright the mouth ends) is the owner's to
judge.

### After the first real start (same day): firmware 0.7.0

The owner's wake-word start `0fc38daa`: "the clip lines up perfectly" (`READY` went out 13.4 s after the
lights began; `LIVE_STARTUP_SECONDS` is now 13.4), "but the mouth startup animation gets full bright too
quickly. It needs to start mostly off and sparkle on from the center outwards. It should go solid
brightness during the same moment the chest LED animation finishes on the normal pattern." Then, in turn:
a slow, dimmer effect for the mouth when not speaking; the eyes dark behind the visor until he is ready,
then blinking on; and a wink the model can call, with a slight head tilt.

- **Mouth at startup, eyes, resting mouth, wink command:** LED firmware 0.7.0
  ([what each does](../firmware/djr3x_leds/README.md#startup)). The host is unchanged for the first three:
  it still sends the eye colour at start and every three seconds, and the board keeps the eyes dark until
  `READY`.
- **The wink is a move of Rex's, not a light effect.** `servo_poses` has a `wink` head expression (a cock
  of the head half the size of `tilt`, held 0.45 s, then back; to his left, the side of the eye that
  winks, since the owner saw the first one cock right while his left eye went dark); when it runs, the output owner sends
  `H:WINK` (`LEDs.wink`) as the move begins. The model reaches it two ways: as the `wink` expression of
  `respond_with_gesture`, with a line, and as the one-shot `wink` step of `set_robot_pose`, which is what
  both providers chose when asked outright. `animate_lights` says a wink is not its to give. A dark LED
  board does not stop the head, and with no servos the eye still winks.
- **Wording.** The shared tool text (`semantics.py`) and the shared show-a-feeling note in
  `adapters/hosted.py` name the wink; the owner asked for it on ChatGPT as well as Claude.

Evidence: 117 firmware checks (17 for the head: the sparkle from the centre, solid at the hand-over, eyes,
the resting ripple, the wink); blocked-I/O checks for the wink through both tools, the owner's `wink` and
the mirror. Paid probes, three calls each (`check_action_requests.py --only
wink_at_me,can_you_wink,plain_sum_no_wink`): Claude 1 of 3 before the pose step existed (it tilted and
blinked both eyes, or said both eyes were the best it could do), then 3 of 3; ChatGPT 3 of 3. Both chose
the `set_robot_pose` step; an unasked wink through `respond_with_gesture` was not probed. Compiled
(420,688 bytes), flashed, and the board answers `hello djr3x_leds 0.7.0`. The owner has since seen the wink on the robot: the board's plain
`WINK` darkens his left eye, and the head now cocks left with it (not yet seen again). **Not yet reported
on the robot:** the rest of 0.7.0.

### The mouth follows the startup sound: firmware 0.8.0 (still November 21)

The owner, after a start on 0.7.0: "the mouth starts lighting up right away. It should start 2 second
after the sound clip starts. It also needs to be much less of the grid lit up at first, almost like 1 or 2
LEDs twinkling on here and there for a few seconds, then the current animation. The startup sound rises
until about 3 seconds in then starts dying around 4 seconds a little then it starts going back up around
6.5 seconds. The mouth LEDs should match that."

- **What he hears, measured.** The clip's plain level does not have that shape (it is back up by 5 s, on
  low rumble); its level as heard does: up to 3.0 s, a dip at 4.0 s, a plateau, above the earlier peak
  from 6.5 s and loudest at 10.5 s. `startup_audio.loudness` measures it that way without numpy (the lows
  turned down by a gentle high-pass at 1 kHz, a reading every 0.1 s over a 24 dB range; it tracks the
  A-weighted level on this clip with correlation 0.98, the plain level 0.74). It takes about 20 ms.
- **In step with the sound as heard.** The startup sound is written about 1.2 s before it is heard.
  `StartupAudio` tells the LED owner when its first sound is due (`on_build_heard`), and from that moment
  `LEDs` sends the head `STARTUP_SOUND:1000,6500,11790` once, then `STARTUP_LEVEL` as the loudness changes,
  until `ready()`. The two moments (dark for 1 s, growing from 6.5 s) are the owner's, kept beside the
  clip's name in `startup_audio.py`; the third is the build's length. The wait was 2 s in the owner's
  first start on 0.8.0 (run `c5de246d`: "it worked fine"), then "a little sooner, maybe by a second";
  the program sends it, so no reflash was needed (the LED menu's own Startup still waits 2 s).
- **What the mouth does** is the firmware's ([README](../firmware/djr3x_leds/README.md#startup)): dark,
  then a light or two twinkling with the loudness (one or two up to the 3 s peak, none in the dip at 4 s,
  one or two on the plateau), then the centre-out sparkle from 6.5 s, about half lit as the build ends,
  solid 0.9 s after `READY`.

Evidence: 122 firmware checks (the wait, the twinkles' number, length and spread, their following the
loudness, the growth, a host that says nothing); blocked-I/O checks for the loudness measure on the
owner's clip and for the owner sending the moments and levels at the heard time. Flashed; in the owner's
next start (run `c5de246d`) the board answered as 0.8.0, the sound was announced to the lights and `READY`
went out at 13.6 s with no LED fault, and the owner reported it worked. **Not yet seen:** the one-second
wait.

## November 24: a darker resting mouth, livelier talking, a moving charge meter, a start that builds

Four owner requests in one sitting, all in the LED board's firmware (0.9.0;
[what each does](../firmware/djr3x_leds/README.md)). No change to what the program sends.

- **Resting mouth.** "When he's not talking you added a pulsating effect that is dim and subtle. I'd like
  that to be changed so that the edges are dark and it is brighter towards the middle, keeping the whole
  'not talking' animation dark." The glow is now in the middle of the mouth only, 32 of 80 lights, the
  middle at 5 to 7.5 % and the outermost lights always dark; it still breathes about every six seconds.
- **Talking mouth.** "Right now I've noticed sometimes it's got this cool pattern and other times it's not
  as cool. Can you improve how it looks?" Cause, measured on the 151 cached startup lines
  (`local/cache/startup-tts`, his real voice) replayed as `LEDs.tick` reports them: the bars' height
  followed the level against the top of its range, his voice mostly sits at a fifth of that, so the tall
  bars showed in the loudest lines only (26 lines through the old code: the mouth shut or nearly so in
  half the frames while he spoke, a bar at three of its five half-rows in one frame in eight; through the
  new: one in eleven, and four in five). The bars are now measured against how loud the voice has lately
  been; each sound opens the middle and rolls out to the corners; a bar is brightest at the middle and
  dimmer towards its tip; the glow and the bars fade into each other.
- **Chest charge meter.** "Right now discharging just blinks the last LED but it would be cool if the
  whole panel animated that it was draining or charging" ("by cool I dont mean cold/blue, I mean
  intersting"). Off the charger a drop of light falls from the top lit light through the whole meter every
  4.4 s over a slow downward ripple; on the charger bands of light climb from the bottom and go on over
  the empty lights as a faint glow. Every light keeps its own colour; the pale blue climbing light is gone.
- **Chest at startup.** "The startup routine is also really cool, but the chest LED patterns get too
  bright too quickly. When they first start the first 2 seconds of the Chest LED pattern needs to be less
  lit up, more like it's barely starting to light up." The sparks and the bars' lights open at a tenth of
  their brightness, fewer of them, and build over four seconds. In the stand-in run of a 13.2 s start the
  panel's light in each of the first four half-seconds is 1, 2, 4 and 16 % of what it was.
- **Found on the way.** `READY` did not always fill the three bars at once: a bar that had just slipped
  waited out its pause first, up to 0.6 s. The earlier check passed by the luck of its fixed random
  numbers. Fixed.
- **Dashboard.** The avatar's mouth mirrors the new resting glow and talking bars
  (`gui/lights.MouthAnimation`); the chest meter's movement is not mirrored.

Evidence: `tools/run_firmware_checks.py`, 144 checks, both strips' real code against the stand-in LED
library with a fake clock (13 new and 4 rewritten for the four changes; the frames prove what is sent to
the strip, not how it looks). The talking measurements replay saved audio through that stand-in, not the
board. The dashboard checks pass (one new, needing numpy). Compiled (426,412 bytes), flashed to `led_port`
under the hardware hand-over; the board answers `hello djr3x_leds 0.9.0` and the battery menu then
repainted the meter (`C:CHARGE:86:0`, so the draining meter was showing).

Shown to the owner the same evening, lights only (a startup timing itself, the resting mouth, three cached
lines and a happy one replayed silently as levels, the meter as if charging at 40 %): "I love it all",
with one change. **Not yet seen:** the chest's start in a real startup, and the mouth on a line he hears.

### Drops without a break: firmware 0.9.1 (still November 24)

Owner, on the draining meter: "I noticed a few second delay between discharge runner gradient, can it just
be continuous without a break?" A drop fell every 4.4 s and took about two, so the meter rested between
them. Now the next drop leaves the top lit light as the last one runs out at the bottom: 2.1 s apart on a
full meter, less on a shorter one, and never under 1.4 s, a short meter's drop falling more slowly so a
nearly empty meter does not flutter. Evidence: the same 144 firmware checks, the draining ones rewritten
(a drop always on the meter, breaks of 0.2 s at most as one forms; the short meter's pace). Compiled
(426,456 bytes), flashed, the board answers `hello djr3x_leds 0.9.1`, and the battery menu repainted the
meter (`C:CHARGE:85:0`). **Not yet seen by the owner.**

### The nine squares join the meter: firmware 0.9.2 (still November 24)

Owner: "there are 3 clusters of 3 LEDs behind square white diffuse covers. Can you add those to the
battery meter, making their color match the charge level but make them have a subtle star-wars pulsing
effect. the colors can be slightly different from one another in the same class of color. right now state
of charge is 85% so they should be some version of the light blue", and "just to be clear the color should
reflect the charge". The nine squares (four lights behind each cover) were dark under the meter. Now each
glows in the colour the meter has at the charge, a slightly different shade of it from its neighbours,
fading slowly up and down in its own time ([README](../firmware/djr3x_leds/README.md#chest-charge-meter)).
They add about 36 dim lights to what the meter draws while he is off. The dashboard does not mirror them.

Evidence: 145 firmware checks (one new: the colour family at 85 % and at 20 %, one colour across a square,
each square's own pace, never dark, nothing else lit, none on an empty pack; three earlier checks that
required the squares dark now require them lit). Compiled (427,324 bytes), flashed; the board answers
`hello djr3x_leds 0.9.2` (on a second asking: the first, straight after the upload, went unanswered) and
was sent `C:CHARGE:85:0`. **Not yet seen by the owner.**

### A brighter edge to the resting glow: firmware 0.9.3 (still November 24)

Owner: "I like the idle mouth gradient but it looks very dim. Can you bring up the brightness on the edges
a little?" The glow's middle is unchanged; it falls away more gently, so the lights two out from the middle
roughly double (to about half the middle's brightness) and four more lights are lit, still none on the
outermost ring. The dashboard's mouth follows. Evidence: 145 firmware checks and the dashboard checks
(the resting-mouth ones updated). Flashed; the board answers `hello djr3x_leds 0.9.3`. Shown to the
owner for 20 s, lights only.

Then (0.9.4) "you could go a little brighter in the center": the whole glow a third brighter, the four
middle lights at 6 to 10 % (were 5 to 7.5), the shape unchanged. Same checks, limits raised; flashed, the
board answers `hello djr3x_leds 0.9.4`, shown to the owner for 20 s.

## November 25: the charge meter comes back when Rex stops

Owner question: Rex was off on batteries and the chest showed no charge. An animation click in the LED
menu the evening before had switched it out of Battery Meter Mode, which is saved, so the LED menu had held
the board ever since and every one of the battery menu's charge sends failed. Now, whenever the LED menu sees
Rex stop, it switches back to Battery Meter Mode and lets go of the board, and the battery menu repaints the
meter within about five seconds. Clicking an animation while he is off still takes the board, as before.

Checked with a fake board and lock (`tests/test_operator_tools.py`: held while off, released for Rex, not
taken back after he stops; fails without the change). The LED menu was restarted with the change; not yet
seen after a real shutdown.
