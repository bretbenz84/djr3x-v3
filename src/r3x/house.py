"""House devices through Homebridge (owner request, November 17).

The house's smart devices run through Homebridge on a Raspberry Pi on the home network (plain HTTP,
home network only): the Honeywell thermostat (homebridge-tcc), Hue lights, Kasa and Tuya plugs,
switches and bulbs. Its UI server's REST API lists them (GET /api/accessories, one entry per service)
and changes one characteristic at a time (PUT /api/accessories/{uniqueId}); Homebridge runs in insecure
mode (-I) so that it can.

Rex logs in with his own non-admin UI account from local/secrets.toml (owner: never the admin one). A
login whose token says admin is refused and the token dropped. Tokens last eight hours; a 401 logs in
again once.

Only a person's request changes anything: the house_devices tool is offered on people's turns only, the
state owner runs it, and Rex's line about a change is spoken only after Homebridge accepted every write.
Locks, garage doors, doors, windows, blinds and alarms can be checked, never changed.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import base64
import json
import math
import threading
import time
from urllib.parse import quote

from .contracts import ProviderFailure
from .providers import Capability

REFRESH_SECONDS = 300          # The device list (names and what each can do); states are read fresh.
RETRY_SECONDS = 30             # After Homebridge did not answer (Pi rebooting, or macOS still asking
                               # whether Python may reach the home network), try again soon.
RETRY_STATUSES = frozenset({'HomebridgeUnreachable', 'HomebridgeTimeout'})
READ_TIMEOUT, WRITE_TIMEOUT = 5., 12.   # A thermostat change goes out through Honeywell's servers.
RESPONSE_LIMIT = 2_000_000
MAX_PARALLEL = 6

KINDS = {'Lightbulb': 'light', 'Switch': 'switch', 'Outlet': 'plug', 'Fan': 'fan', 'Fanv2': 'fan',
         'Thermostat': 'thermostat', 'HeaterCooler': 'heater or air conditioner', 'AirPurifier': 'air purifier',
         'HumidifierDehumidifier': 'humidifier', 'Television': 'TV',
         'TemperatureSensor': 'temperature sensor', 'HumiditySensor': 'humidity sensor',
         'ContactSensor': 'door or window sensor', 'MotionSensor': 'motion sensor',
         'OccupancySensor': 'occupancy sensor', 'LeakSensor': 'leak sensor', 'SmokeSensor': 'smoke alarm',
         'CarbonMonoxideSensor': 'carbon monoxide alarm', 'LightSensor': 'light sensor',
         'AirQualitySensor': 'air quality sensor', 'LockMechanism': 'lock', 'GarageDoorOpener': 'garage door',
         'SecuritySystem': 'alarm system', 'Door': 'door', 'Window': 'window', 'WindowCovering': 'blinds'}
# Checked, never changed: a voice in the room (or a TV) must not open the house.
CHECK_ONLY = frozenset({'LockMechanism', 'GarageDoorOpener', 'SecuritySystem', 'Door', 'Window', 'WindowCovering'})
ACTIONS = ('check', 'turn_on', 'turn_off', 'set_brightness', 'set_color', 'set_temperature', 'heat', 'cool', 'auto')
MODE_CODES = {'heat': 1, 'cool': 2, 'auto': 3}
MODES = {0: 'off', 1: 'heat', 2: 'cool', 3: 'auto'}
RUNNING = {0: 'idle', 1: 'heating', 2: 'cooling'}
# Hue and saturation for a named color; whites by color temperature (mireds) where a light has it.
COLORS = {'red': (0, 100), 'orange': (28, 100), 'yellow': (55, 100), 'green': (120, 100), 'teal': (175, 100),
          'blue': (235, 100), 'purple': (275, 100), 'pink': (325, 60)}
WHITES = {'warm_white': (370, (35, 30)), 'white': (250, (0, 0)), 'cool_white': (182, (220, 8))}
COLOR_NAMES = (*COLORS, *WHITES)
UNAVAILABLE = {
    'HomebridgeAccountIsAdmin': "Rex's Homebridge account is an admin account, which he will not use; the owner has "
                                'to give him his own non-admin Homebridge account',
    'HomebridgeLoginRefused': 'Homebridge refused his login (wrong username or password)',
    'HomebridgeUnreachable': 'Homebridge is not answering', 'HomebridgeTimeout': 'Homebridge is not answering',
    'NoHouseDevices': 'Homebridge lists no devices he can use'}


def fahrenheit(celsius):
    return None if not isinstance(celsius, (int, float)) else round(celsius * 9 / 5 + 32, 1)


def _characteristic(service, kind, writable=False):
    return next((c for c in service.get('serviceCharacteristics') or ()
                 if isinstance(c, dict) and c.get('type') == kind and (c.get('canWrite') or not writable)), None)


def _valid_codes(characteristic):
    valid = characteristic.get('validValues')
    if isinstance(valid, list):
        return set(valid)
    low, high = characteristic.get('minValue'), characteristic.get('maxValue')
    return set(range(int(low), int(high) + 1)) if isinstance(low, int) and isinstance(high, int) else set()


def abilities(service):
    """The changes a service accepts, in a fixed order (check is always possible)."""
    if service.get('type') in CHECK_ONLY:
        return ()
    found = []
    if _characteristic(service, 'On', True) or _characteristic(service, 'Active', True):
        found += ['turn_on', 'turn_off']
    mode = _characteristic(service, 'TargetHeatingCoolingState', True)
    if mode:
        codes = _valid_codes(mode)
        if 0 in codes and 'turn_off' not in found:
            found.append('turn_off')
        found += [action for action, code in MODE_CODES.items() if code in codes]
    if _characteristic(service, 'Brightness', True):
        found.append('set_brightness')
    if (_characteristic(service, 'Hue', True) and _characteristic(service, 'Saturation', True)
            or _characteristic(service, 'ColorTemperature', True)):
        found.append('set_color')
    if _characteristic(service, 'TargetTemperature', True):
        found.append('set_temperature')
    return tuple(found)


def _notes(service):
    """Action -> how the listing names it, where the device limits it."""
    notes = {}
    target = _characteristic(service, 'TargetTemperature', True)
    if target and isinstance(target.get('minValue'), (int, float)) and isinstance(target.get('maxValue'), (int, float)):
        notes['set_temperature'] = (f'set_temperature {round(fahrenheit(target["minValue"]))}-'
                                    f'{round(fahrenheit(target["maxValue"]))} F')
    if (_characteristic(service, 'ColorTemperature', True)
            and not (_characteristic(service, 'Hue', True) and _characteristic(service, 'Saturation', True))):
        notes['set_color'] = 'set_color (whites only)'
    return notes


@dataclass(frozen=True)
class Device:
    name: str
    unique_id: str
    kind: str
    service: dict = field(repr=False, compare=False)


def catalog(services, hidden=()):
    """Name -> Device for the services Rex can use. A thermostat's own temperature sensor repeats the
    thermostat's reading and is left out; repeated names get their accessory's name, then a number."""
    hidden = {h.casefold().strip() for h in hidden}
    usable = [s for s in services if isinstance(s, dict) and s.get('type') in KINDS
              and isinstance(s.get('uniqueId'), str) and s['uniqueId'] and isinstance(s.get('serviceName'), str)
              and s['serviceName'].strip() and s['serviceName'].casefold().strip() not in hidden]

    def accessory(s):
        return ((s.get('instance') or {}).get('username') or (s.get('instance') or {}).get('port'), s.get('aid'))
    thermostats = {accessory(s) for s in usable if s['type'] == 'Thermostat'}
    usable = [s for s in usable if not (s['type'] == 'TemperatureSensor' and accessory(s) in thermostats)]
    counts = {}
    for s in usable:
        counts[s['serviceName'].strip()] = counts.get(s['serviceName'].strip(), 0) + 1
    devices = {}
    for s in usable:
        name = s['serviceName'].strip()
        if counts[name] > 1:
            owner = ((s.get('accessoryInformation') or {}).get('Name') or '').strip()
            if owner and owner != name:
                name = f'{name} ({owner})'
        base, number = name, 2
        while name in devices:
            name, number = f'{base} {number}', number + 1
        devices[name] = Device(name, s['uniqueId'], KINDS[s['type']], s)
    return devices


def listing(devices):
    """What the model is offered: name -> kind and the changes it accepts (any device can be checked)."""
    out = {}
    for name, device in devices.items():
        notes = _notes(device.service)
        words = ', '.join(notes.get(action, action) for action in abilities(device.service))
        out[name] = device.kind + (': ' + words if words else '')
    return out


def _nearest_color(hue):
    def distance(name):
        gap = abs(COLORS[name][0] - hue) % 360
        return min(gap, 360 - gap)
    return min(COLORS, key=distance)


def report(device, service):
    """A device's readings in plain words (temperatures in Fahrenheit)."""
    values = service.get('values') if isinstance(service.get('values'), dict) else {}
    out = {'name': device.name, 'kind': device.kind}
    put = out.__setitem__
    if 'On' in values:
        put('on', None if values['On'] is None else bool(values['On']))
    elif 'Active' in values:
        put('on', None if values['Active'] is None else bool(values['Active']))
    if 'Brightness' in values:
        put('brightness_pct', values['Brightness'])
    saturation = values.get('Saturation')
    if isinstance(saturation, (int, float)) and saturation >= 25 and isinstance(values.get('Hue'), (int, float)):
        put('color', _nearest_color(values['Hue']))
    elif isinstance(values.get('ColorTemperature'), (int, float)):
        mired = values['ColorTemperature']
        put('color', 'warm white' if mired >= 300 else 'cool white' if mired <= 210 else 'white')
    elif 'Hue' in values:
        put('color', 'white')
    if 'TargetHeatingCoolingState' in values:
        put('mode', MODES.get(values['TargetHeatingCoolingState']))
    if 'CurrentHeatingCoolingState' in values:
        put('running', RUNNING.get(values['CurrentHeatingCoolingState']))
    if 'CurrentTemperature' in values:
        put('temperature_f', fahrenheit(values['CurrentTemperature']))
    if 'TargetTemperature' in values:
        put('set_to_f', fahrenheit(values['TargetTemperature']))
    for key, name in (('CurrentRelativeHumidity', 'humidity_pct'), ('RotationSpeed', 'speed_pct'),
                      ('OutletInUse', 'in_use'), ('MotionDetected', 'motion'), ('OccupancyDetected', 'occupied'),
                      ('LeakDetected', 'leak'), ('SmokeDetected', 'smoke'), ('CarbonMonoxideDetected', 'carbon_monoxide'),
                      ('CurrentAmbientLightLevel', 'light_lux'), ('CurrentPosition', 'open_pct'),
                      ('BatteryLevel', 'battery_pct'), ('StatusLowBattery', 'low_battery'), ('StatusFault', 'fault')):
        if key in values:
            value = values[key]
            put(name, bool(value) if name in {'in_use', 'motion', 'occupied', 'leak', 'smoke', 'carbon_monoxide',
                                              'low_battery', 'fault'} and value is not None else value)
    if 'ContactSensorState' in values:
        put('contact', {0: 'closed', 1: 'open'}.get(values['ContactSensorState']))
    if 'LockCurrentState' in values:
        put('lock', {0: 'unlocked', 1: 'locked', 2: 'jammed'}.get(values['LockCurrentState'], 'unknown'))
    if 'CurrentDoorState' in values:
        put('door', {0: 'open', 1: 'closed', 2: 'opening', 3: 'closing', 4: 'stopped'}.get(values['CurrentDoorState']))
    if 'SecuritySystemCurrentState' in values:
        put('alarm', {0: 'armed, home', 1: 'armed, away', 2: 'armed, night', 3: 'disarmed',
                      4: 'going off'}.get(values['SecuritySystemCurrentState']))
    return out


def plan(device, action, value, color):
    """The characteristic writes, in order, that carry out one change on one device; ValueError when it cannot."""
    service = device.service
    if action not in abilities(service):
        if device.service.get('type') in CHECK_ONLY:
            raise ValueError('CheckOnly')
        if action == 'turn_on' and 'heat' in abilities(service):
            raise ValueError('SayHeatOrCool')
        raise ValueError({'turn_on': 'CannotSwitch', 'turn_off': 'CannotSwitch', 'set_brightness': 'NotDimmable',
                          'set_color': 'NoColors', 'set_temperature': 'NoTemperatureSetting'}.get(action, 'ModeNotSupported'))
    power = _characteristic(service, 'On', True)
    if action in {'turn_on', 'turn_off'}:
        if power:
            return [('On', action == 'turn_on')]
        if _characteristic(service, 'Active', True):
            return [('Active', 1 if action == 'turn_on' else 0)]
        return [('TargetHeatingCoolingState', 0)]
    if action in MODE_CODES:
        # "Set the heat to 68" is one request: the mode, then the target it heats or cools to.
        return [('TargetHeatingCoolingState', MODE_CODES[action]),
                *(_target(device, value) if value is not None else ())]
    if action == 'set_brightness':
        if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 100:
            raise ValueError('BrightnessIsOneToHundredPercent')
        brightness = _characteristic(service, 'Brightness', True)
        low, high = brightness.get('minValue', 0), brightness.get('maxValue', 100)
        steps = [('Brightness', int(min(max(round(value), low), high)))]
        return steps + ([('On', True)] if power else [])
    if action == 'set_color':
        if color not in COLOR_NAMES:
            raise ValueError('ColorMissing')
        warmth = _characteristic(service, 'ColorTemperature', True)
        tinted = _characteristic(service, 'Hue', True) and _characteristic(service, 'Saturation', True)
        if color in WHITES and warmth:
            low, high = warmth.get('minValue', 140), warmth.get('maxValue', 500)
            steps = [('ColorTemperature', int(min(max(WHITES[color][0], low), high)))]
        elif tinted:
            hue, saturation = COLORS[color] if color in COLORS else WHITES[color][1]
            steps = [('Hue', hue), ('Saturation', saturation)]
        else:
            raise ValueError('WhitesOnly')
        return steps + ([('On', True)] if power else [])
    return _target(device, value)


def _target(device, value):
    """A target temperature in Fahrenheit, written in Celsius at the device's own step and range."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('TemperatureMissing')
    target = _characteristic(device.service, 'TargetTemperature', True)
    if target is None:
        raise ValueError('NoTemperatureSetting')
    step = target.get('minStep') if isinstance(target.get('minStep'), (int, float)) and target['minStep'] > 0 else 0.1
    celsius = round(round((value - 32) * 5 / 9 / step) * step, 2)
    low, high = target.get('minValue'), target.get('maxValue')
    if (isinstance(low, (int, float)) and celsius < low - 1e-6) or (isinstance(high, (int, float)) and celsius > high + 1e-6):
        raise ValueError('TemperatureOutOfRange')
    return [('TargetTemperature', celsius)]


def _claims(token):
    try:
        part = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError):
        raise ProviderFailure('HomebridgeLoginUnreadable') from None


class House:
    """One Homebridge client: the device list refreshed in the background, states read and changes made
    on request. No device is touched at start, refresh or close."""

    def __init__(self, settings, username, password, clock=time.time, transport=None):
        self.url = settings.homebridge_url.rstrip('/')
        self.hidden = tuple(settings.homebridge_hidden_devices)
        self.username, self._password = username, password
        self.clock, self.transport = clock, transport
        self.capability = Capability('house_devices', 'Homebridge UI REST API',
            "the house's lights, plugs and thermostat, checked and changed when a person asks", None, ('httpx',),
            ('one HTTP client on the home network; device list every 5 minutes (about 30 ms), 30 s after no answer; '
             '5 s reads, 12 s writes; <=2 MB per response',), required=False)
        self.trace = None
        self.client = None
        self.lock, self.token_lock = threading.Lock(), threading.Lock()
        self.stop, self.wake = threading.Event(), threading.Event()
        self.worker = None
        self.devices, self.status = None, None   # Name -> Device after the first good refresh; last failure.
        self._token, self._token_until = None, 0.

    def connect(self):
        """The HTTP client only: no request is made until something is asked of Homebridge."""
        if not self.username.strip() or not self._password:
            raise ProviderFailure('HomebridgeCredentialsMissing')
        import httpx
        self.client = httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT), follow_redirects=False,
                                   **({'transport': self.transport} if self.transport else {}))

    def start(self):
        self.connect()
        self.stop.clear()
        self.worker = threading.Thread(target=self._run, name='r3x-house-devices', daemon=True)
        self.worker.start()

    def cancel(self):
        self.stop.set()
        self.wake.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(READ_TIMEOUT + 2)
            self.worker = None
        if self.client:
            self.client.close()
            self.client = None
        with self.token_lock:
            self._token = None

    # ------------------------------------------------------------------ HTTP

    def _login(self):
        import httpx
        try:
            response = self.client.post(self.url + '/api/auth/login',
                                        json={'username': self.username, 'password': self._password})
        except httpx.TimeoutException:
            raise ProviderFailure('HomebridgeTimeout') from None
        except httpx.HTTPError:
            raise ProviderFailure('HomebridgeUnreachable') from None
        if response.status_code in (401, 403):
            raise ProviderFailure('HomebridgeLoginRefused')
        if response.status_code >= 400:
            raise ProviderFailure(f'HomebridgeLoginFailed:{response.status_code}')
        try:
            body = response.json()
            token = body['access_token']
        except (ValueError, KeyError, TypeError):
            raise ProviderFailure('HomebridgeLoginUnreadable') from None
        if _claims(token).get('admin') is not False:
            raise ProviderFailure('HomebridgeAccountIsAdmin')   # The token is dropped unused.
        lasts = body.get('expires_in') if isinstance(body.get('expires_in'), (int, float)) else 3600
        return token, self.clock() + max(60., lasts - 300.)

    def _token_now(self, stale=None):
        with self.token_lock:
            if self._token is None or self._token == stale or self.clock() >= self._token_until:
                self._token, self._token_until = None, 0.
                self._token, self._token_until = self._login()
            return self._token

    def _request(self, method, path, body=None, timeout=READ_TIMEOUT):
        import httpx
        if self.client is None:
            raise ProviderFailure('HouseNotStarted')
        stale = None
        for _ in range(2):
            token = self._token_now(stale)
            try:
                response = self.client.request(method, self.url + path, json=body, timeout=timeout,
                                               headers={'Authorization': 'Bearer ' + token})
            except httpx.TimeoutException:
                raise ProviderFailure('HomebridgeTimeout') from None
            except httpx.HTTPError:
                raise ProviderFailure('HomebridgeUnreachable') from None
            if response.status_code == 401:
                stale = token   # Expired or revoked: log in again, once.
                continue
            if len(response.content) > RESPONSE_LIMIT:
                raise ProviderFailure('HomebridgeResponseLimit')
            return response
        raise ProviderFailure('HomebridgeLoginRefused')

    def _json(self, response):
        try:
            return response.json()
        except ValueError:
            raise ProviderFailure('HomebridgeResponseUnreadable') from None

    def services(self):
        response = self._request('GET', '/api/accessories')
        if response.status_code >= 400:
            raise ProviderFailure(f'HomebridgeError:{response.status_code}')
        data = self._json(response)
        if not isinstance(data, list):
            raise ProviderFailure('HomebridgeResponseUnreadable')
        return data

    def _service(self, unique_id, write=None):
        path = '/api/accessories/' + quote(unique_id, safe='')
        if write is None:
            response = self._request('GET', path)
        else:
            response = self._request('PUT', path, {'characteristicType': write[0], 'value': write[1]}, WRITE_TIMEOUT)
        if response.status_code >= 400:
            detail = None
            try:
                detail = str(response.json().get('message'))[:200]
            except (ValueError, AttributeError):
                pass
            return None, detail or f'Homebridge answered {response.status_code}'
        data = self._json(response)
        return (data, None) if isinstance(data, dict) else (None, 'unreadable answer')

    # ------------------------------------------------------------------ device list

    def refresh(self):
        try:
            devices = catalog(self.services(), self.hidden)
        except ProviderFailure as exc:
            self._status(str(exc))
            return
        with self.lock:
            self.devices = devices
        self._status(None if devices else 'NoHouseDevices', len(devices))

    def _status(self, status, count=None):
        with self.lock:
            changed, self.status = status != self.status, status
        if changed and self.trace:
            self.trace.emit('house_devices_status', status=status or 'ok', devices=count)

    def _run(self):
        while not self.stop.is_set():
            self.refresh()
            with self.lock:
                status = self.status
            self.wake.wait(RETRY_SECONDS if status in RETRY_STATUSES else REFRESH_SECONDS)
            self.wake.clear()

    def offer(self):
        """The reply context's house entry: devices on offer, or why there are none; None before the first try."""
        from .field_guide import GUIDE
        with self.lock:
            devices, status = self.devices, self.status
        if devices:
            return {'devices': listing(devices), 'meaning': GUIDE['house']}
        if status:
            return {'unavailable': UNAVAILABLE.get(status, 'Homebridge is not working right now'),
                    'meaning': GUIDE['house']}
        return None

    # ------------------------------------------------------------------ requests

    def run(self, action, names, value=None, color=None, cancel=None):
        """(outcome, reason, data) for one house_devices call. Nothing is written unless the call names
        offered devices; a device that cannot make the change is reported and left alone."""
        try:
            return self._run_call(action, names, value, color, cancel)
        except ProviderFailure as exc:
            return 'failed', str(exc), {}

    def _run_call(self, action, names, value, color, cancel):
        if action not in ACTIONS:
            return 'rejected', 'UnknownHouseAction', {}
        with self.lock:
            devices = self.devices
        if not devices:
            # The house was out of reach at the last try (November 17: macOS had not yet let Python onto the
            # home network). A person is asking now, so try again now rather than at the next refresh.
            self.refresh()
            with self.lock:
                devices, status = self.devices, self.status
            if not devices:
                status = status or 'NoHouseDevices'
                return 'failed', status, {'unavailable': UNAVAILABLE.get(status, 'Homebridge is not working right now')}
        names = list(dict.fromkeys(names))
        unknown = [n for n in names if n not in devices]
        if not names or unknown:
            self.wake.set()   # Perhaps added since the last refresh.
            return 'rejected', 'UnknownDevice', {'unknown': unknown, 'devices': sorted(devices)}
        chosen = [devices[n] for n in names]
        if action == 'check':
            return self._check(chosen)
        plans, refused = {}, {}
        for device in chosen:
            try:
                plans[device.name] = plan(device, action, value, color)
            except ValueError as exc:
                refused[device.name] = str(exc)
        if not plans:
            reasons = set(refused.values())
            return 'rejected', reasons.pop() if len(reasons) == 1 else 'CannotDoThat', {
                'changed': [], 'not_changed': [{'name': n, 'why': r} for n, r in refused.items()]}
        if cancel:
            cancel.check()
        with ThreadPoolExecutor(min(MAX_PARALLEL, len(plans)), thread_name_prefix='r3x-house-write') as pool:
            done = dict(zip(plans, pool.map(lambda name: self._apply(devices[name], plans[name]), plans)))
        changed = [{'name': n, 'now': r['now']} for n, r in done.items() if r['done']]
        failed = [{'name': n, 'why': 'DeviceRefused', 'detail': r['detail'],
                   **({'partly_changed': True} if r['partly'] else {})} for n, r in done.items() if not r['done']]
        data = {'action': action, 'changed': changed,
                'not_changed': [*failed, *({'name': n, 'why': r} for n, r in refused.items())]}
        if not data['not_changed']:
            return 'ok', 'executed', data
        return 'failed', 'NotEverythingChanged' if changed else 'NothingChanged', data

    def _apply(self, device, steps):
        service = None
        for index, step in enumerate(steps):
            try:
                service, detail = self._service(device.unique_id, step)
            except ProviderFailure as exc:
                service, detail = None, str(exc)
            if service is None:
                return {'done': False, 'detail': detail, 'partly': index > 0}
        return {'done': True, 'now': report(device, service)}

    def _check(self, chosen):
        if len(chosen) == 1:
            service, detail = self._service(chosen[0].unique_id)
            if service is None:
                return 'failed', 'HomebridgeRefused', {'detail': detail}
            found = {chosen[0].unique_id: service}
        else:
            found = {s.get('uniqueId'): s for s in self.services() if isinstance(s, dict)}
        readings = [report(d, found[d.unique_id]) if d.unique_id in found else {'name': d.name, 'missing': True}
                    for d in chosen]
        return 'ok', 'executed', {'devices': readings,
                                  'checked_at': time.strftime('%I:%M %p', time.localtime(self.clock())).lstrip('0')}


FIXES = {'HomebridgeAccountIsAdmin': 'In the Homebridge UI, add a user account with admin rights turned off and put its '
                                     'username and password in local/secrets.toml (homebridge_username, homebridge_password).',
         'HomebridgeCredentialsMissing': 'Put Rex\'s non-admin Homebridge username and password in local/secrets.toml.',
         'HomebridgeLoginRefused': 'Check homebridge_username and homebridge_password in local/secrets.toml.',
         'HomebridgeUnreachable': 'Check homebridge_url and that the Homebridge UI answers on the home network.',
         'HomebridgeTimeout': 'Check homebridge_url and that the Homebridge UI answers on the home network.'}


def check_setup(settings, username, password, transport=None):
    """The owner's check (python -m r3x house): log in and list what Rex would be offered, with what each
    device reads now. Reads only; nothing in the house changes."""
    if not settings.homebridge_url:
        return {'outcome': 'failed', 'reason': 'HomebridgeUrlMissing',
                'fix': 'Set homebridge_url in local/settings.toml, e.g. "http://192.168.1.211:8581".'}
    house = House(settings, username, password, transport=transport)
    try:
        house.connect()
        services = house.services()
    except ProviderFailure as exc:
        return {'outcome': 'failed', 'reason': str(exc), 'fix': FIXES.get(str(exc), 'See docs/HOUSE_DEVICES.md.')}
    finally:
        house.close()
    devices = catalog(services, house.hidden)
    offered = listing(devices)
    used = {d.unique_id for d in devices.values()}
    return {'outcome': 'ok', 'account': 'not an admin',
            'devices': {name: {'offered_as': offered[name], 'reads_now': report(d, d.service)}
                        for name, d in devices.items()},
            'not_offered': sorted({f"{s.get('serviceName')} ({s.get('type')})" for s in services
                                   if isinstance(s, dict) and s.get('uniqueId') not in used
                                   and s.get('type') not in {'ProtocolInformation', 'History'}})}
