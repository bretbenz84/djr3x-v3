"""Rex's own power and room readings for the systems_status tool (October 21 owner request).

The drive controller reports battery, charger and its BME280 climate sensor (DriveBase.base_status).
That sensor sits inside Rex beside warm electronics: October 21 it read 87 F in a 72 F house, so
the room temperature carries the configured offset and the humidity is recomputed for it.
"""
import math


def _vapour_hpa(celsius):
    """Saturation vapour pressure (Magnus formula, over water)."""
    return 6.112 * math.exp(17.62 * celsius / (243.12 + celsius))


def room(reading, offset_f):
    """Room climate from the internal sensor: same moisture, cooler air."""
    if not reading or reading.get('temp_c') is None:
        return None
    inside_f = reading['temp_c'] * 9 / 5 + 32
    room_f = inside_f + offset_f
    room_c = (room_f - 32) * 5 / 9
    humidity = reading.get('humidity_pct')
    if isinstance(humidity, (int, float)) and 0 <= humidity <= 100:
        humidity = round(min(100., humidity * _vapour_hpa(reading['temp_c']) / _vapour_hpa(room_c)))
    else:
        humidity = None
    return {'temperature_f': round(room_f), 'temperature_c': round(room_c, 1), 'humidity_pct': humidity,
            'pressure_hpa': round(reading['pressure_hpa']) if isinstance(reading.get('pressure_hpa'), (int, float)) else None,
            'meaning': 'The room around you, from the climate sensor inside your body, corrected for the heat of '
                       'your electronics. You can tell how warm and humid it is in here; this is not outdoor weather.'}


def report(status, offset_f):
    """The model's view of the base's live readings. Unknown values stay null; nothing is estimated."""
    if not status or not status.get('readings', True) or 'battery' not in status:
        return {'connected': bool(status and status.get('connected')), 'battery': None, 'room': None,
                'meaning': 'The wheel controller is not reporting, so battery and room readings are unavailable right now.'}
    battery = dict(status['battery'])
    amps, volts = battery.get('amps_drawn'), battery.get('volts')
    battery.pop('percent_meaning', None)
    battery['watts'] = round(amps * volts, 1) if amps is not None and volts is not None else None
    battery['current_direction'] = (None if amps is None else 'into the battery' if amps < 0 else 'out of the battery')
    return {'connected': status.get('connected'), 'reading_age_s': status.get('reading_age_s'), 'battery': battery,
            'room': room(status.get('room'), offset_f), 'controller': status.get('controller'),
            'tilt_deg': status.get('tilt_deg'),
            'meaning': 'Live readings from your drive controller. battery.percent is the controller\'s coulomb-counted '
                       'estimate (null: not yet known). amps_drawn is positive while the pack powers you and negative '
                       'while it charges; on_charger is the controller\'s charger flag (plugged in). Say the numbers '
                       'the way a person would ("about 80 percent", "twelve point eight volts").'}
