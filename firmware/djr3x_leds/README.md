# DJ-R3X combined LED board

One classic ESP32 driving both LED strips. It is the board V3's LED owner (`src/r3x/leds.py`)
and the LED and battery menus talk to, on the port set as `led_port`. Where the code came
from: [V2_REFERENCE.md](../../V2_REFERENCE.md), "Combined LED board firmware". How the robot
uses it and what is still unverified: [LED integration](../../docs/LED_INTEGRATION.md).

## Wiring

| Signal | ESP32 pin | Through the 74AHCT125N | Strip |
| --- | --- | --- | --- |
| Head data | GPIO 18 | in pin 2, out pin 3 | 82 pixels: 2 eyes, then the 80-pixel mouth |
| Chest data | GPIO 19 | in pin 5, out pin 6 | 98 pixels: three panels and two chest lights |
| Ground | any GND | pin 7 | joined to the LED 5 V supply ground |

The ESP32's 3.3 V data goes through a 74AHCT125N level shifter, which sends the strips a
5 V signal. Its other pins: 14 to the 5 V buck rail (not the ESP32's 3.3 V pin, or it
outputs 3.3 V); 1 and 4, which switch on the two channels used, to ground; the unused
inputs 9, 10, 12 and 13 to ground; 8 and 11 unconnected. A 74HCT245 or a breakout board
has a different pinout.

The ESP32 is powered by its USB cable and supplies data only. Both strips take 5 V from
the buck rail; do not join that rail to the board's 5 V pin while USB is connected. One
ground joins the ESP32, the shifter, the buck rail and both strips: without it the head
showed one flickering light blue. With the data straight from the 3.3 V pins the head
looked right on the bench (November 14); with the shifter in (November 17) the owner
reports the lights "no longer flicker at all".

## Protocol

115200 baud, ASCII, one command per line. Both strips are dark after boot until commanded.

| Line | Effect |
| --- | --- |
| `HELLO` | Replies `hello djr3x_leds <version> head=82@18 chest=98@19` |
| `H:<command>` | Head only; commands in the header of `head.cpp` (among them `WINK`) |
| `C:<command>` | Chest only; commands above `command()` in `chest.cpp` |
| `<command>` | Both strips; each ignores what it does not know |

`STARTUP` on either strip may carry the time the host expects its start to take, in milliseconds
(`STARTUP:12800`), and is then ended by `READY`; see [Startup](#startup).

The board prints the same identity line, starting `ready`, once after boot and sends
nothing else. A line longer than 63 characters is dropped whole. Bare `CHARGE:<soc>` reaches
only the head and bare `CHARGE:<soc>:<0|1>` reaches both, because the two forms differ.

## Build and flash

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/djr3x_leds
```

```bash
arduino-cli upload -p <port> --fqbn esp32:esp32:esp32 firmware/djr3x_leds
```

Version 0.9.0, built with the `esp32:esp32` core 3.3.10 and FastLED 3.10.3: 426,412 bytes of
program, 27,152 bytes of globals. Each strip has its own FastLED controller and brightness,
since the library's board-wide show, brightness and clear would act on both.

## Startup

Since 0.6.0 (owner, November 21, 2026) startup is paced to Rex's own start. `STARTUP:<ms>` says how long
the host expects to need (3 to 60 s) and `READY` says it is ready.

- **Chest.** The sparks thin out over the time given. Since 0.9.0 (owner, November 24: "too bright too
  quickly"; the first two seconds "barely starting to light up") it opens faintly: the sparks and the
  bars' lights begin at a tenth of their ordinary brightness, the sparks fewer too, and build over the
  first third of the time given (four seconds at most): about a sixth at one second, a third at two. The
  two chest lights come up with them. Before, the first two seconds were the brightest of the sparkle.
  `READY` ends the opening, and the spark that burns the charge meter is not part of it. Under the sparks
  each of the three bars lights from the
  bottom at its own pace (A red, B blue, C white) and struggles: its top light flickers and sometimes goes
  out, the next one up tries to catch, the bar browns out for a moment, loses a light or two and climbs
  back. No bar holds its eighth light before `READY`. The nine blocks come on, each with its stutter, in
  the last three seconds before the planned end. `READY` fills all three bars at once, bright (since 0.9.0
  also a bar that had just slipped, which could wait 0.6 s first), and over
  0.9 s they ease to steady; then the ordinary panel takes them over with a blend.
- **Mouth** (0.8.0; the owner asked for less, later, and in step with the startup sound). After
  `H:STARTUP` the mouth is dark and follows the sound the host plays: `H:STARTUP_SOUND:<wait>,<grow>,<end>`
  says the sound has just begun and gives three moments in it, in milliseconds from then, and
  `H:STARTUP_LEVEL:<0-255>` says how loud it is as it goes. Until `wait` the mouth stays dark. Then a light
  or two twinkle on and off, here and there over the whole mouth, each for about a third of a second: more
  of them when the sound is louder (about two and a half at once at level 160), none under level 60. From
  `grow` the mouth is lit in the ordinary amber from its centre outwards: a front moves out, slowly at
  first, reaching about half the mouth at `end`; a light it has passed holds, dimly (7 to 13 %), the
  lights just ahead of it sparkle (brighter when the sound is louder), and elsewhere the twinkles go on.
  Told nothing about the sound (the LED menu's bare `STARTUP`, or a host silent for 4 s), it assumes the
  sound began 1.6 s after `STARTUP` with moments 2 s and 6.5 s. The
  edges wait for `READY`, which sends the front out to the corners in 0.65 s while the lit lights brighten;
  0.9 s after `READY`, as the chest's bars are handed to its ordinary panel, every light is solid at a
  quarter of full brightness. It stays solid 0.6 s (the chest's blend into the panel), then the resting
  mouth takes over and eases down from there. A `SPEAK:` takes the mouth at once. `ACTIVE`, `IDLE`,
  `SPEAK_STOP` and `EYE:` do not cut the sparkle short.
- **Eyes** (0.7.0). They are dark from `STARTUP`, behind the shut visor; an `EYE:` meanwhile only sets the
  colour they will open with (warm gold if none is sent). After `READY` they stay dark 0.25 s, flash twice,
  the second brighter, and are on for good 0.65 s after `READY`, while the visor lifts. A `SPEAK:` during
  startup brings them on at once.
- **Without a host.** Bare `STARTUP` (the LED menu) times itself: 12.8 s, then the same lock. With a time
  and no `READY`, both strips keep going 10 s past the plan and then lock by themselves.

## Resting mouth

Since 0.9.0 (owner, November 24: "the edges are dark and it is brighter towards the middle, keeping the
whole not-talking animation dark") the mouth at rest (`ACTIVE`, `IDLE`, after `SPEAK_STOP`) glows in its
middle only, in the current feeling's colour: the four middle lights between 5 and 7.5 %, falling away
quickly (about half of that one light out, a fifth two lights out, 1 % on the last lit lights), 32 of the
80 lights lit and the outermost ring always dark. The glow is six lights across and six down, about as wide as it is tall
on the panel. It still breathes about once every six seconds, each breath spreading from the middle
outwards. From 0.7.0 (owner, November 21: a slow effect when not speaking, dimmer) it was a ripple over
the whole mouth, every light between 3 and 8 %; before that the whole mouth pulsed together.

## Talking mouth

The equalizer bars: eight columns opening above and below the middle pair of rows, a falling dot over
each, never over 45 %. Since 0.9.0 (owner, November 24: "sometimes it's got this cool pattern and other
times it's not as cool"):

- **The voice is measured against itself.** A bar's height used to follow `SPEAK_LEVEL` against the top
  of its range. His voice sits far below that: over 151 of his cached lines, measured as the host reports
  them, half the voiced moments were under level 48 and a line's loudest moment was anywhere from 91 to
  230. With 26 of those lines replayed through the old code, the mouth was shut or nearly so (no bar past
  one of its five half-rows) in half the frames while he spoke, and a bar reached three half-rows in one
  frame in eight: the tall dancing bars showed only in the loudest lines. Now the level is taken as a
  share of how loud the voice has lately been (followed up within a tenth of a second, let back down over
  about four, never under level 77), with quiet sounds lifted. On the same lines the mouth is shut in one
  frame in eleven and a bar reaches three half-rows in four frames in five, four in nearly three in five.
  Under level 13 is silence, and a steady hiss just above it does not open the mouth.
- **Each sound rolls outward.** The two middle columns follow the voice now and each column further out
  follows it 60 ms later, so a sound opens the middle of the mouth and runs out to the corners, and its
  end leaves the middle first.
- **Depth.** A bar is brightest at the middle of the mouth and falls to 30 % of that at its outermost
  half-row; the dot above it is brighter than the tip under it. A louder sound is brighter as well as
  taller. The shut line between sounds is brightest in the middle (10 %), fainter at its ends, with a slow
  shimmer along it.
- **In and out.** The resting glow fades out under the first bars of a line (0.2 s) and the last bars
  close into the glow (0.35 s) instead of the mouth jumping between the two. A line that cuts startup
  short still takes the mouth at once.

## Wink

Since 0.7.0 `H:WINK` darkens one eye for 0.45 s and brings it back as it then is; the other eye, the
mouth and the chest are untouched. The right eye winks unless `H:WINK:L` is sent. It is ignored while the
eyes are dark (off, asleep, charging, fading, or behind the visor at startup).

## Chest transitions

Since 0.2.0 the chest blends every change of state from the frame that was showing; only `OFF`
is instant. `STARTUP` sent while the charge meter shows sets a spark climbing the meter before
the sparkle, and the blocks come on piece by piece under the last sparks. `IDLE`, `ACTIVE`,
`SPEAK:neutral` and `SPEAK_STOP` sent during startup take effect when it ends (at `READY`, or by
itself as above); send `OFF` first to stop a startup early. The "Transitions" comment in `chest.cpp` has the timings.

Since 0.3.0 `FADEOFF:<soc>:<0|1>` is the shutdown fade ending in the charge meter: the meter
sweeps up under the last two seconds of the fade. A `CHARGE` within a minute of the fade's end
confirms it without a visible change; otherwise the meter fades out after that minute. Plain
`FADEOFF` still ends dark. Under the meter the two small chest lights stay dark (they used to
blip gold for one frame every few seconds).

## Feelings

`SPEAK:<feeling>` on either strip takes one of ten names; an unknown one is treated as neutral.
The head speaks in the feeling's colour and keeps it for the idle glow; the chest runs its
pattern until `SPEAK_STOP`. The eyes are set by the host (`EYE:r,g,b`). Since 0.5.0:

| Feeling | Mouth | Chest |
| --- | --- | --- |
| neutral | amber | the ordinary panel |
| happy | cyan | gold heads bouncing on the bars, cheerful pops, confetti |
| excited | yellow | gold heads racing up the bars, rapid pops, glitter |
| sad | blue | slow blue sighs |
| angry | red | red alert: scanner bars, alarm blocks, slams |
| curious | purple | **new:** a violet head drifting over each bar, one block lit in turn |
| surprised | icy white | **new:** every bar bursts from its middle and every block comes on, blue with white tips; the blocks flicker and settle over 2.5 s |
| proud | deep gold | **new:** bars fill from the bottom and stay full, blocks come on in turn and stay; nothing blinks |
| skeptical | sour yellow-green | **new:** the panel stops: one flat dim line on each bar, two blocks; the last bar's line rises two pixels every 3.6 s (an eyebrow) |
| sympathetic | rose | **new:** a soft heartbeat, two beats and a rest, the middle panel leading |

The burst is never a white panel: an earlier full-panel white browned the chest supply out and
showed red, so its body is blue and white is kept to the bar tips and a block or two.

## Chest charge meter

`C:CHARGE:<soc>:<0|1>` shows the charge on the three bars as one meter of 24 lights, bar A's bottom to bar
C's top, each light in its own colour from red to blue, lit up to the charge. Since 0.9.0 (owner,
November 24: "discharging just blinks the last LED but it would be cool if the whole panel animated that
it was draining or charging") the whole meter moves, in brightness only, so the level still reads by where
the colours stop:

- **Off the charger (`:0`), draining.** A slow ripple runs down the lit lights all the time. Every 4.4 s
  the top lit light swells, then lets go of a drop: a band of brighter light that falls from it through
  every lit light, gathering speed, and runs out at the bottom, while the top light is left dim and
  slowly comes back. Nothing above the fill line is ever lit.
- **On the charger (`:1`), filling.** The ripple runs up. Every 2.4 s a band of brighter light enters at
  the bottom and climbs through the lit lights; above the fill line it goes on to the top as a faint glow
  in each empty light's own colour. At 100 % only the ripple is left.

The movement eases in over 1.2 s after the sweep that brings the meter on, and no light jumps between two
frames. Before, the top lit light blinked once a second and, on the charger, one pale blue light climbed
from the fill line. The blocks and the two chest lights stay dark under the meter, as before.

## Mouth charge meter

Since 0.4.0 `H:CHARGE:<soc>` shows a meter on the mouth's ten rows, like the chest's: each row
has its own colour, red at the bottom to blue at the top, and the rows light from the bottom up
to the charge (one row for each 10 %, rounded up). A soft band of light climbs from the bottom
row to the top in 2 s, rests 1 s, and repeats; above the charge it lights the empty rows faintly
in their own colours. The rows come on from the bottom over 1.2 s when the mode is entered; a new
level while it shows only moves the fill line. Lit rows hold 10 % brightness and reach 39 % under
the band. Before 0.4.0 the whole mouth breathed in one of five colours for the charge.

Which end of the grid is the top is taken from the dashboard's 3D model of the head (row 0, the
first eight mouth pixels, widest and highest) and has not been checked against the PCB: if red
shows at the top, set `MOUTH_ROW0_AT_TOP` to 0 in `head.cpp`.

```bash
.venv/bin/python tools/run_firmware_checks.py
```

runs `chest.cpp` and `head.cpp` on the Mac against `firmware/tests/fake_fastled` and checks the
frames they would send. It does not show how they look on the strip.
