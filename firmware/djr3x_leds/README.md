# DJ-R3X combined LED board

One classic ESP32 driving both LED strips. It is the board V3's LED owner (`src/r3x/leds.py`)
and the LED and battery menus talk to, on the port set as `led_port`. Where the code came
from: [V2_REFERENCE.md](../../V2_REFERENCE.md), "Combined LED board firmware". How the robot
uses it and what is still unverified: [LED integration](../../docs/LED_INTEGRATION.md).

## Wiring

| Signal | ESP32 pin | Strip |
| --- | --- | --- |
| Head data | GPIO 18 | 82 pixels: 2 eyes, then the 80-pixel mouth |
| Chest data | GPIO 19 | 98 pixels: three panels and two chest lights |
| Ground | any GND | joined to the LED 5 V supply ground |

The ESP32 is powered by its USB cable and supplies data only. Both strips take 5 V from
the buck rail; do not join that rail to the board's 5 V pin while USB is connected. The
ground wire is required: without it the head showed one flickering light blue. The data
pins are 3.3 V with no level shifter; the head ran correctly that way on the bench
(November 14), with the servos still.

## Protocol

115200 baud, ASCII, one command per line. Both strips are dark after boot until commanded.

| Line | Effect |
| --- | --- |
| `HELLO` | Replies `hello djr3x_leds <version> head=82@18 chest=98@19` |
| `H:<command>` | Head only; commands in the header of `head.cpp` |
| `C:<command>` | Chest only; commands above `command()` in `chest.cpp` |
| `<command>` | Both strips; each ignores what it does not know |

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

Version 0.4.0, built with the `esp32:esp32` core 3.3.10 and FastLED 3.10.3: 413,984 bytes of
program, 26,448 bytes of globals. Each strip has its own FastLED controller and brightness,
since the library's board-wide show, brightness and clear would act on both.

## Chest transitions

Since 0.2.0 the chest blends every change of state from the frame that was showing; only `OFF`
is instant. `STARTUP` sent while the charge meter shows sets a spark climbing the meter before
the sparkle, and the panel comes on piece by piece under the last sparks. `IDLE`, `ACTIVE`,
`SPEAK:neutral` and `SPEAK_STOP` sent during startup take effect when it ends (about 13 s); send
`OFF` first to stop a startup early. The "Transitions" comment in `chest.cpp` has the timings.

Since 0.3.0 `FADEOFF:<soc>:<0|1>` is the shutdown fade ending in the charge meter: the meter
sweeps up under the last two seconds of the fade. A `CHARGE` within a minute of the fade's end
confirms it without a visible change; otherwise the meter fades out after that minute. Plain
`FADEOFF` still ends dark. Under the meter the two small chest lights stay dark (they used to
blip gold for one frame every few seconds).

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

runs `chest.cpp` on the Mac against `firmware/tests/fake_fastled` and checks the frames it
would send. It does not show how they look on the strip.
