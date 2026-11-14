"""The LED board (firmware/djr3x_leds) as the operator menus use it.

One ESP32 drives the head and chest strips on one serial port. Every command names its
strip: `H:` for the head (eyes and mouth), `C:` for the chest panels. The board answers
HELLO with its name and version, so a menu never sends light commands to some other
device on a mistyped port, and never has to guess how long the board takes to be ready.
"""
import os

import v3_settings

FIRMWARE = 'djr3x_leds'
BAUD = 115200
HELLO_ATTEMPTS = 10   # About 3 s: each unanswered read waits out the 0.1 s timeout.


def port() -> str:
    """The configured LED board port, or '' when the lights are not set up."""
    return (os.environ.get('LED_PORT') or v3_settings.env().get('LED_PORT') or '').strip()


def connect(path: str):
    """Open the board; return the serial handle once it has identified itself.

    Raises OSError when the port cannot be opened or nothing on it answers as the LED
    board. The port is closed again before the error is raised.
    """
    import serial
    handle = serial.Serial(path, BAUD, timeout=0.1, write_timeout=1.0, exclusive=True)
    try:
        for _ in range(HELLO_ATTEMPTS):
            handle.write(b'HELLO\n')
            for _ in range(3):
                fields = handle.readline().decode('ascii', 'replace').split()
                # "ready" is the same line, printed by a board that has just booted.
                if len(fields) >= 3 and fields[0] in ('hello', 'ready') and fields[1] == FIRMWARE:
                    return handle
        raise OSError(f'nothing on {path} answered as the {FIRMWARE} board')
    except BaseException:
        handle.close()
        raise
