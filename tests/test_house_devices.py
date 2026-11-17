"""House devices through Homebridge (owner request, November 17). A fake Homebridge UI server behind httpx's
in-memory transport: synthetic devices shaped like the real list (the TCC thermostat's characteristics as read
from the house on November 17), no network and no real device."""
import base64
import json
import time
import unittest
from unittest import mock
from urllib.parse import unquote

import httpx

from r3x.contracts import Cancellation, Cancelled, ToolCall, Transcript
from r3x.field_guide import GUIDE
from r3x.house import House, catalog, check_setup, listing, report
from r3x.output import TextOutput
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from r3x.settings import Settings
from tests.test_memory_core import MemoryFixture, observation

URL = 'http://homebridge.test:8581'


def characteristic(kind, value, fmt, write=True, **extra):
    return {'type': kind, 'value': value, 'format': fmt, 'canRead': True, 'canWrite': write, **extra}


def service(uid, kind, name, chars, aid, owner=None):
    return {'uniqueId': uid, 'type': kind, 'humanType': kind, 'serviceName': name, 'aid': aid, 'iid': 8,
            'instance': {'name': 'homebridge', 'username': '0E:AA:BB:CC:DD:EE', 'port': 51826},
            'accessoryInformation': {'Name': owner or name}, 'serviceCharacteristics': chars,
            'values': {c['type']: c['value'] for c in chars}}


def house_services():
    return [
        service('protocol', 'ProtocolInformation', 'Homebridge 1234', [characteristic('Version', '1.1.0', 'string', False)], 1),
        service('thermostat', 'Thermostat', 'THERMOSTAT', [
            characteristic('CurrentHeatingCoolingState', 0, 'uint8', False, validValues=[0, 1, 2]),
            characteristic('TargetHeatingCoolingState', 0, 'uint8', minValue=0, maxValue=3, validValues=[0, 1, 2]),
            characteristic('CurrentTemperature', 21.7, 'float', False, unit='celsius'),
            characteristic('TargetTemperature', 19.4, 'float', unit='celsius', minValue=4.4, maxValue=37.2, minStep=0.1),
            characteristic('TemperatureDisplayUnits', 0, 'uint8')], 2),
        service('thermostat-sensor', 'TemperatureSensor', 'THERMOSTAT Temperature',
                [characteristic('CurrentTemperature', 21.7, 'float', False)], 2, 'THERMOSTAT'),
        service('outside', 'TemperatureSensor', 'Outside Temperature',
                [characteristic('CurrentTemperature', 53.3, 'float', False)], 3, 'THERMOSTAT'),
        service('kitchen', 'Lightbulb', 'Kitchen Light', [
            characteristic('On', True, 'bool'), characteristic('Brightness', 80, 'int', minValue=0, maxValue=100),
            characteristic('Hue', 240, 'float', minValue=0, maxValue=360), characteristic('Saturation', 90, 'float'),
            characteristic('ColorTemperature', 300, 'uint32', minValue=153, maxValue=500)], 4),
        service('lamp', 'Lightbulb', 'Lamp', [
            characteristic('On', False, 'bool'), characteristic('Brightness', 100, 'int'),
            characteristic('ColorTemperature', 250, 'uint32', minValue=153, maxValue=370)], 5),
        service('coffee', 'Outlet', 'Coffee Maker', [characteristic('On', 1, 'bool'),
                                                     characteristic('OutletInUse', 1, 'bool', False)], 6),
        service('strip-1', 'Outlet', 'Outlet', [characteristic('On', False, 'bool')], 7, 'Power Strip'),
        service('strip-2', 'Outlet', 'Outlet', [characteristic('On', False, 'bool')], 8, 'Power Strip'),
        service('front-door', 'LockMechanism', 'Front Door', [
            characteristic('LockCurrentState', 1, 'uint8', False), characteristic('LockTargetState', 1, 'uint8')], 9),
    ]


def token(admin):
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')
    return '.'.join((part({'alg': 'HS256'}), part({'username': 'rex', 'admin': admin, 'exp': 2_000_000_000}), 'sig'))


class FakeHomebridge:
    """The Homebridge UI's login and accessory endpoints, recording every write."""

    def __init__(self, admin=False):
        self.admin, self.down = admin, False
        self.services = {s['uniqueId']: s for s in house_services()}
        self.tokens, self.logins, self.writes, self.refuse = set(), 0, [], {}
        self.requests = 0

    def __call__(self, request):
        self.requests += 1
        if self.down:
            raise httpx.ConnectError('down', request=request)
        path = request.url.path
        if path == '/api/auth/login':
            body = json.loads(request.content)
            if (body.get('username'), body.get('password')) != ('rex', 'hunter2'):
                return httpx.Response(401, json={'message': 'Unauthorized'})
            self.logins += 1
            issued = token(self.admin) + str(self.logins)
            self.tokens.add(issued)
            return httpx.Response(201, json={'access_token': issued, 'token_type': 'Bearer', 'expires_in': 28800})
        if request.headers.get('authorization', '').removeprefix('Bearer ') not in self.tokens:
            return httpx.Response(401, json={'message': 'Unauthorized', 'statusCode': 401})
        if path == '/api/accessories':
            return httpx.Response(200, json=list(self.services.values()))
        uid = unquote(path.removeprefix('/api/accessories/'))
        found = self.services.get(uid)
        if found is None:
            return httpx.Response(400, json={'message': f"Service with uniqueId of '{uid}' not found."})
        if request.method == 'PUT':
            body = json.loads(request.content)
            if uid in self.refuse:
                return httpx.Response(400, json={'message': self.refuse[uid]})
            self.writes.append((uid, body['characteristicType'], body['value']))
            found['values'][body['characteristicType']] = body['value']
        return httpx.Response(200, json=found)


def eventually(condition, seconds=2.):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.01)
    return False


def started(fake, hidden=(), password='hunter2'):
    house = House(Settings(homebridge_url=URL, homebridge_hidden_devices=hidden), 'rex', password,
                  clock=lambda: 1_000_000., transport=httpx.MockTransport(fake))
    house.connect()   # No worker thread: each check refreshes by hand.
    house.refresh()
    return house


class CatalogTests(unittest.TestCase):
    def test_the_offered_list_names_each_device_and_what_it_takes(self):
        offered = listing(catalog(house_services()))
        self.assertEqual(offered, {
            'THERMOSTAT': 'thermostat: turn_off, heat, cool, set_temperature 40-99 F',
            'Outside Temperature': 'temperature sensor',
            'Kitchen Light': 'light: turn_on, turn_off, set_brightness, set_color',
            'Lamp': 'light: turn_on, turn_off, set_brightness, set_color (whites only)',
            'Coffee Maker': 'plug: turn_on, turn_off',
            'Outlet (Power Strip)': 'plug: turn_on, turn_off', 'Outlet (Power Strip) 2': 'plug: turn_on, turn_off',
            'Front Door': 'lock'})   # Checked, never changed. The thermostat's own sensor and plumbing are left out.

    def test_hidden_devices_are_neither_offered_nor_reachable(self):
        self.assertNotIn('Outside Temperature', catalog(house_services(), ('outside temperature',)))

    def test_readings_are_in_plain_words_and_fahrenheit(self):
        devices = catalog(house_services())
        read = {name: report(d, d.service) for name, d in devices.items()}
        self.assertEqual(read['THERMOSTAT'], {'name': 'THERMOSTAT', 'kind': 'thermostat', 'mode': 'off', 'running': 'idle',
                                              'temperature_f': 71.1, 'set_to_f': 66.9})
        self.assertEqual((read['Kitchen Light']['on'], read['Kitchen Light']['brightness_pct'], read['Kitchen Light']['color']),
                         (True, 80, 'blue'))
        self.assertEqual((read['Lamp']['on'], read['Lamp']['color']), (False, 'white'))
        self.assertEqual((read['Coffee Maker']['on'], read['Coffee Maker']['in_use']), (True, True))
        self.assertEqual(read['Front Door']['lock'], 'locked')


class AccountTests(unittest.TestCase):
    def test_an_admin_account_is_refused_and_rex_says_why(self):
        fake = FakeHomebridge(admin=True)
        house = started(fake)
        self.addCleanup(house.close)
        offer = house.offer()
        self.assertNotIn('devices', offer)
        self.assertIn('admin', offer['unavailable'])
        self.assertEqual(house.run('turn_off', ['Kitchen Light'])[:2], ('failed', 'HomebridgeAccountIsAdmin'))
        self.assertEqual(fake.writes, [])
        result = check_setup(Settings(homebridge_url=URL), 'rex', 'hunter2', transport=httpx.MockTransport(fake))
        self.assertEqual((result['outcome'], result['reason']), ('failed', 'HomebridgeAccountIsAdmin'))
        self.assertIn('admin rights turned off', result['fix'])

    def test_a_wrong_password_and_a_silent_homebridge_are_reasons_not_crashes(self):
        house = started(FakeHomebridge(), password='wrong')
        self.addCleanup(house.close)
        self.assertIn('refused his login', house.offer()['unavailable'])
        fake = FakeHomebridge()
        house = started(fake)
        self.addCleanup(house.close)
        fake.down = True
        self.assertEqual(house.run('check', ['THERMOSTAT']), ('failed', 'HomebridgeUnreachable', {}))
        house.refresh()
        self.assertIn('devices', house.offer())   # The last good list stays; a change would report the failure.

    def test_a_request_while_the_house_was_out_of_reach_tries_it_again_then(self):
        fake = FakeHomebridge()
        fake.down = True
        house = started(fake)
        self.addCleanup(house.close)
        self.assertIn('not answering', house.offer()['unavailable'])
        self.assertEqual(house.run('turn_on', ['Lamp']),
                         ('failed', 'HomebridgeUnreachable', {'unavailable': 'Homebridge is not answering'}))
        fake.down = False   # The owner allowed Python onto the home network.
        outcome, reason, data = house.run('turn_on', ['Lamp'])
        self.assertEqual((outcome, reason), ('ok', 'executed'))
        self.assertEqual(fake.writes, [('lamp', 'On', True)])
        self.assertIn('devices', house.offer())

    def test_a_homebridge_that_answers_again_is_used_within_the_retry_not_the_five_minute_refresh(self):
        # November 17 live run: macOS held Python off the home network until the owner allowed it; Rex would
        # then have said he could not reach the house for up to five more minutes.
        fake = FakeHomebridge()
        fake.down = True
        house = House(Settings(homebridge_url=URL), 'rex', 'hunter2', transport=httpx.MockTransport(fake))
        with mock.patch('r3x.house.RETRY_SECONDS', .02), mock.patch('r3x.house.REFRESH_SECONDS', 60):
            house.start()
            self.addCleanup(house.close)
            self.assertTrue(eventually(lambda: 'not answering' in (house.offer() or {}).get('unavailable', '')))
            fake.down = False
            self.assertTrue(eventually(lambda: 'devices' in (house.offer() or {})))

    def test_a_refused_login_is_not_retried_every_thirty_seconds(self):
        fake = FakeHomebridge()
        house = House(Settings(homebridge_url=URL), 'rex', 'wrong', transport=httpx.MockTransport(fake))
        with mock.patch('r3x.house.RETRY_SECONDS', .02), mock.patch('r3x.house.REFRESH_SECONDS', 60):
            house.start()
            self.addCleanup(house.close)
            self.assertTrue(eventually(lambda: (house.offer() or {}).get('unavailable')))
            time.sleep(.2)
        self.assertEqual(fake.requests, 1)

    def test_an_expired_token_logs_in_again_once(self):
        fake = FakeHomebridge()
        house = started(fake)
        self.addCleanup(house.close)
        fake.tokens.clear()   # Homebridge restarted or the eight hours ran out.
        self.assertEqual(house.run('check', ['Lamp'])[0], 'ok')
        self.assertEqual(fake.logins, 2)

    def test_the_owner_check_lists_what_rex_would_be_offered_and_changes_nothing(self):
        fake = FakeHomebridge()
        result = check_setup(Settings(homebridge_url=URL, homebridge_hidden_devices=('Outside Temperature',)),
                             'rex', 'hunter2', transport=httpx.MockTransport(fake))
        self.assertEqual(result['outcome'], 'ok')
        self.assertEqual(result['devices']['THERMOSTAT']['reads_now']['set_to_f'], 66.9)
        self.assertIn('Outside Temperature (TemperatureSensor)', result['not_offered'])
        self.assertEqual(fake.writes, [])
        self.assertEqual(check_setup(Settings(), 'rex', 'hunter2')['reason'], 'HomebridgeUrlMissing')


class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeHomebridge()
        self.house = started(self.fake)
        self.addCleanup(self.house.close)

    def test_starting_and_listing_touch_no_device(self):
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(self.house.offer()['meaning'], GUIDE['house'])

    def test_a_light_is_switched_and_the_result_says_how_it_is_now(self):
        outcome, reason, data = self.house.run('turn_off', ['Kitchen Light'])
        self.assertEqual((outcome, reason), ('ok', 'executed'))
        self.assertEqual(self.fake.writes, [('kitchen', 'On', False)])
        self.assertEqual(data['changed'][0]['now']['on'], False)

    def test_the_thermostat_is_set_in_its_own_units_and_mode_only_when_asked(self):
        self.assertEqual(self.house.run('set_temperature', ['THERMOSTAT'], 68)[0], 'ok')
        self.assertEqual(self.fake.writes, [('thermostat', 'TargetTemperature', 20.0)])
        self.fake.writes.clear()
        self.assertEqual(self.house.run('heat', ['THERMOSTAT'], 70)[0], 'ok')   # "Set the heat to 70."
        self.assertEqual(self.fake.writes, [('thermostat', 'TargetHeatingCoolingState', 1),
                                            ('thermostat', 'TargetTemperature', 21.1)])
        self.fake.writes.clear()
        self.assertEqual(self.house.run('turn_off', ['THERMOSTAT'])[0], 'ok')
        self.assertEqual(self.fake.writes, [('thermostat', 'TargetHeatingCoolingState', 0)])

    def test_dimming_turns_the_light_on_and_cooling_sets_the_mode(self):
        self.assertEqual(self.house.run('set_brightness', ['Lamp'], 30.4)[0], 'ok')
        self.assertEqual(self.fake.writes, [('lamp', 'Brightness', 30), ('lamp', 'On', True)])
        self.fake.writes.clear()
        self.assertEqual(self.house.run('cool', ['THERMOSTAT'])[0], 'ok')
        self.assertEqual(self.fake.writes, [('thermostat', 'TargetHeatingCoolingState', 2)])

    def test_what_a_device_cannot_do_is_refused_before_anything_is_written(self):
        self.fake.writes.clear()
        for action, names, value, reason in (
                ('auto', ['THERMOSTAT'], None, 'ModeNotSupported'),         # This thermostat has no auto.
                ('turn_on', ['THERMOSTAT'], None, 'SayHeatOrCool'),
                ('heat', ['THERMOSTAT'], 120, 'TemperatureOutOfRange'),      # Not even the mode changes.
                ('set_temperature', ['THERMOSTAT'], 20, 'TemperatureOutOfRange'),   # Celsius by mistake.
                ('turn_off', ['Front Door'], None, 'CheckOnly'),
                ('set_brightness', ['Coffee Maker'], 50, 'NotDimmable'),
                ('set_brightness', ['Lamp'], 0, 'BrightnessIsOneToHundredPercent'),
                ('turn_off', ['Garage Light'], None, 'UnknownDevice')):
            self.assertEqual(self.house.run(action, names, value)[:2], ('rejected', reason), action)
        self.assertEqual(self.fake.writes, [])

    def test_several_devices_change_together_and_a_partial_result_is_reported(self):
        outcome, reason, data = self.house.run('set_color', ['Kitchen Light', 'Lamp'], color='red')
        self.assertEqual((outcome, reason), ('failed', 'NotEverythingChanged'))
        self.assertEqual(sorted(self.fake.writes), [('kitchen', 'Hue', 0), ('kitchen', 'On', True), ('kitchen', 'Saturation', 100)])
        self.assertEqual(data['not_changed'], [{'name': 'Lamp', 'why': 'WhitesOnly'}])
        self.fake.writes.clear()
        self.assertEqual(self.house.run('set_color', ['Lamp'], color='warm_white')[0], 'ok')
        self.assertEqual(self.fake.writes, [('lamp', 'ColorTemperature', 370), ('lamp', 'On', True)])
        self.assertEqual(self.house.run('turn_on', ['Outlet (Power Strip)', 'Outlet (Power Strip) 2'])[0], 'ok')

    def test_a_device_that_refuses_the_write_is_not_reported_changed(self):
        self.fake.refuse['coffee'] = 'Device did not respond'
        outcome, reason, data = self.house.run('turn_off', ['Coffee Maker'])
        self.assertEqual((outcome, reason), ('failed', 'NothingChanged'))
        self.assertEqual(data['not_changed'], [{'name': 'Coffee Maker', 'why': 'DeviceRefused', 'detail': 'Device did not respond'}])

    def test_a_cancelled_request_writes_nothing(self):
        cancel = Cancellation()
        cancel.event.set()   # A stop word arrived while the call was being written.
        with self.assertRaises(Cancelled):
            self.house.run('turn_off', ['Kitchen Light'], cancel=cancel)
        self.assertEqual(self.fake.writes, [])

    def test_a_check_reads_fresh_states(self):
        self.fake.services['lamp']['values']['On'] = True   # Switched by hand since the list was read.
        outcome, _, data = self.house.run('check', ['Lamp'])
        self.assertEqual((outcome, data['devices'][0]['on']), ('ok', True))
        outcome, _, data = self.house.run('check', ['THERMOSTAT', 'Coffee Maker'])
        self.assertEqual([d['name'] for d in data['devices']], ['THERMOSTAT', 'Coffee Maker'])


class SettingsTests(unittest.TestCase):
    def test_the_address_is_scheme_host_and_port_only(self):
        self.assertEqual(Settings(homebridge_url='http://192.168.1.211:8581').homebridge_url, 'http://192.168.1.211:8581')
        for bad in ('192.168.1.211:8581', 'ftp://192.168.1.211', 'http://user:pw@192.168.1.211:8581',
                    'http://192.168.1.211:8581/api', 'http://192.168.1.211:8581?x=1', 'http://192.168.1.211:99999', 7):
            with self.assertRaises(ValueError, msg=bad):
                Settings(homebridge_url=bad)
        self.assertEqual(Settings(homebridge_hidden_devices=['Outside Temperature']).homebridge_hidden_devices,
                         ('Outside Temperature',))
        with self.assertRaises(ValueError):
            Settings(homebridge_hidden_devices=['', 'Lamp'])


class ConversationTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.fake = FakeHomebridge()
        self.state.house = started(self.fake)
        self.addCleanup(self.state.house.close)

    def turn(self, text, call, follow_up='Hmm, that did not take.'):
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                yield follow_up if snapshot.continuation else call
        brain = Brain()
        self.begin(text)
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        return brain, session.process_turn(self.state.current[1], Transcript(text, 0, True), 0, observation())

    def change(self, action, devices, text, value=None, color=None):
        return ToolCall('h', 'house_devices', {'say_first': 'On it.', 'action': action, 'devices': devices,
                                               'value': value, 'color': color, 'text': text, 'expression': None})

    def test_asked_to_switch_a_light_he_says_so_only_after_homebridge_accepted_it(self):
        brain, result = self.turn('Rex, turn off the kitchen light.',
                                  self.change('turn_off', ['Kitchen Light'], 'Kitchen light is off.'))
        context = brain.snapshots[0].context
        self.assertIn('Kitchen Light', context['house']['devices'])
        self.assertIn('house_devices', {d['name'] for d in grounded_tools(context)})
        self.assertEqual(result.completed_text, ['On it.', 'Kitchen light is off.'])
        self.assertEqual(len(brain.snapshots), 1)   # No second model call.
        self.assertEqual(self.fake.writes, [('kitchen', 'On', False)])

    def test_a_change_that_fails_goes_back_to_him_and_its_line_is_never_said(self):
        self.fake.refuse['thermostat'] = 'Honeywell did not answer'
        brain, result = self.turn('Set the heat to 68.', self.change('heat', ['THERMOSTAT'], 'Heat is on, aiming for 68.', 68))
        self.assertEqual(len(brain.snapshots), 2)
        output = next(json.loads(r['output']) for r in brain.snapshots[1].continuation if r.get('type') == 'function_call_output')
        self.assertEqual((output['outcome'], output['data']['not_changed'][0]['detail']), ('failed', 'Honeywell did not answer'))
        self.assertNotIn('Heat is on, aiming for 68.', result.completed_text)

    def test_a_question_reads_the_device_and_he_answers_from_it(self):
        call = ToolCall('h', 'house_devices', {'say_first': 'Checking.', 'action': 'check', 'devices': ['THERMOSTAT'],
                                               'value': None, 'color': None, 'text': None, 'expression': None})
        brain, result = self.turn("What's the thermostat set to?", call, 'About 67, and the heat is off.')
        output = next(json.loads(r['output']) for r in brain.snapshots[1].continuation if r.get('type') == 'function_call_output')
        self.assertEqual(output['data']['devices'][0]['set_to_f'], 66.9)
        self.assertEqual(result.completed_text, ['Checking.', 'About 67, and the heat is off.'])
        self.assertEqual(self.fake.writes, [])

    def test_offered_only_on_a_persons_turn_and_also_while_the_house_is_out_of_reach(self):
        house = self.state.house.offer()
        self.begin('Hello there.')
        base = self.state.context()
        names = lambda extra: {d['name'] for d in grounded_tools({**base, **extra})}
        self.assertIn('house_devices', names({'house': house}))
        self.assertNotIn('house_devices', names({}))
        # November 17: withheld while Homebridge was unreachable, the tool's absence made Claude explain
        # aloud why it could not call it. Offered, a call tries the house again.
        self.assertIn('house_devices', names({'house': {'unavailable': 'Homebridge is not answering'}}))
        for trigger in ('conversation_lull', 'ambient', 'visual_arrival', 'reconsideration'):
            self.assertNotIn('house_devices', names({'house': house, 'trigger': trigger}))

    def test_replay_and_evaluations_never_reach_the_house(self):
        self.state.frozen = True
        self.begin('Turn on the lamp.')
        result = self.call('house_devices', {'say_first': 'Sure.', 'action': 'turn_on', 'devices': ['Lamp'],
                                             'value': None, 'color': None, 'text': 'Lamp on.', 'expression': None})
        self.assertEqual((result.outcome, result.reason), ('rejected', 'FrozenEvaluationDisallowsMutation'))
        self.assertEqual(self.fake.writes, [])

    def test_the_schema_checks_syntax_only(self):
        good = {'say_first': 'Hmm.', 'action': 'set_brightness', 'devices': ['Lamp'], 'value': 40, 'color': None,
                'text': 'Lamp at forty.', 'expression': None}
        validate('house_devices', good)
        for bad in ({**good, 'value': 'dim'}, {**good, 'devices': []}, {**good, 'action': 'unlock'},
                    {**good, 'color': 'mauve'}, {**good, 'value': float('nan')}):
            with self.assertRaises(ValueError):
                validate('house_devices', bad)


if __name__ == '__main__':
    unittest.main()
