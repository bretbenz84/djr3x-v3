"""V3 wiring for the operator companions copied from V2 (September 27, 2026).

The supervisor and the servo/LED/battery menu-bar apps read their ports and audio
devices from V3's `local/settings.toml` instead of V2's `.env`. The keys below are
the V2 names each copied program already looks up, so their logic is unchanged.
Standard library only: these processes start even when the conversation core cannot.
"""
from pathlib import Path
import tomllib

V3_ROOT = Path(__file__).resolve().parents[1]
LOCAL = V3_ROOT / 'local'
STATE = LOCAL / 'operator'           # Console mode, throttle sequences, measurements
LOGS = LOCAL / 'logs'
SETTINGS = LOCAL / 'settings.toml'
VENV_PYTHON = V3_ROOT / '.venv' / 'bin' / 'python'
WAKE_MODELS = LOCAL / 'models' / 'wake'
SOUNDS = V3_ROOT / 'assets' / 'audio' / 'operator'


def env() -> dict[str, str]:
    """V2-named settings derived from V3's settings; unset values are absent."""
    try:
        data = tomllib.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}
    text = lambda key: str(data.get(key) or '').strip()
    values = {
        'AUDIO_DEVICE_NAME': text('input_device_name'),
        'AUDIO_OUTPUT_DEVICE_NAME': text('output_device_name'),
        'MAESTRO_PORT': text('servo_port'),
        'ARDUINO_HEAD_PORT': text('head_led_port'),
        'ARDUINO_CHEST_PORT': text('chest_led_port'),
        'MOTION_ESP32_PORT': text('drive_port'),
    }
    if data.get('input_device_index') is not None:
        values['AUDIO_DEVICE_INDEX'] = str(data['input_device_index'])
    if data.get('microphone_mode') == 'respeaker-flex':
        values['AUDIO_AEC_INPUT_CHANNEL'] = '0'   # V3 and V2 both read the processed channel 0.
    return {key: value for key, value in values.items() if value}
