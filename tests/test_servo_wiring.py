"""Servo wiring (owner, November 23): servo ids in the program, board channels on the wire.

Synthetic port only; nothing here says the robot is wired as the file says.
"""
import tempfile
import unittest
from pathlib import Path

from r3x import servo_wiring
from r3x.adapters.maestro import Maestro
from r3x.contracts import ProviderFailure
from r3x.servo_poses import LIMITS, UNPOWERED_REST


class Port:
    def __init__(self):
        self.written, self.replies = [], {}

    def write(self, packet):
        self.written.append(bytes(packet))
        return len(packet)

    def read(self, count):
        channel = self.written[-1][1]
        return self.replies.get(channel, 0).to_bytes(2, 'little')

    def close(self):
        pass


def wiring_file(text):
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'servo_wiring.toml'
    path.write_text(text)
    return folder, path


class WiringFileTests(unittest.TestCase):
    def test_the_file_lays_rex_out_from_the_top_down(self):
        wiring = servo_wiring.load()
        self.assertEqual(set(wiring), set(LIMITS))
        # visor, head pair, neck, lift; hero arm out to the fingers; throttle; poker.
        top_down = (3, 2, 11, 0, 1, 7, 4, 5, 17, 8, 9, 10, 14, 15, 6, 12, 13, 16)
        self.assertEqual(tuple(sorted(wiring, key=wiring.get)), top_down)

    def test_a_bad_file_is_refused(self):
        good = servo_wiring.PATH.read_text()
        for broken, error in ((good.replace('id = 3\nchannel = 0', 'id = 3\nchannel = 1'), 'ServoWiringIncomplete'),
                              (good.replace('id = 10\nchannel = 11', 'id = 10\nchannel = 14')
                                   .replace('id = 6\nchannel = 14', 'id = 6\nchannel = 11'), 'ServoWiringThrottleNotConsecutive'),
                              (good.replace('id = 3\nchannel = 0', 'id = 3\nchannel = 18'), 'ServoWiringInvalid:visor'),
                              ('', 'ServoWiringMissing')):
            folder, path = wiring_file(broken)
            with folder, self.assertRaisesRegex(ValueError, error):
                servo_wiring.load(path)


class MaestroWiringTests(unittest.TestCase):
    def setUp(self):
        self.port = Port()
        self.seen = []
        self.board = Maestro(self.port, servo_wiring.load())
        self.board.observers = [self.seen.append]

    def test_targets_go_to_the_wired_channel_and_observers_see_servo_ids(self):
        self.board.targets({3: 5000})   # The visor, id 3, is on channel 0.
        self.assertEqual(self.port.written, [bytes((0x84, 0, 5000 & 127, 5000 >> 7))])
        self.assertEqual(self.seen, [bytes((0x84, 3, 5000 & 127, 5000 >> 7))])

    def test_the_throttle_moves_in_one_command_from_its_first_channel(self):
        pose = dict(UNPOWERED_REST)
        self.board.targets({ch: pose[ch] for ch in (8, 9, 10)})
        self.assertEqual(self.port.written[-1][:3], bytes((0x9F, 3, 9)))
        self.assertEqual(self.seen[-1][:3], bytes((0x9F, 3, 8)))

    def test_readings_and_profiles_use_the_wired_channel(self):
        self.port.replies = {17: 6000}   # Channel 17 carries the poker hand's twist, id 16.
        self.assertEqual(self.board.positions((16,)), {16: 6000})
        self.assertEqual(self.port.written[-1], bytes((0x90, 17)))
        self.board.profiles({16: (20, 4)})
        self.assertEqual([packet[:2] for packet in self.port.written[-2:]], [bytes((0x89, 17)), bytes((0x87, 17))])

    def test_without_wiring_the_id_is_the_channel(self):
        port = Port()
        Maestro(port).targets({3: 5000})
        self.assertEqual(port.written, [bytes((0x84, 3, 5000 & 127, 5000 >> 7))])


def servo_menu():
    import importlib
    import sys
    # The menu lives in operator_tools/tools, which shares its package name with the repository's
    # tools; set the repository's aside while the menu (and its throttle_sequences) import.
    root = str(Path(__file__).resolve().parent.parent / 'operator_tools')
    aside = {name: sys.modules.pop(name) for name in list(sys.modules) if name == 'tools' or name.startswith('tools.')}
    sys.path.insert(0, root)
    try:
        menu = importlib.import_module('tools.rex_servo_menubar')
    finally:
        sys.path.remove(root)
        for name in [name for name in sys.modules if name == 'tools' or name.startswith('tools.')]:
            del sys.modules[name]
        sys.modules.update(aside)
    return menu


class MenuWiringTests(unittest.TestCase):
    def test_the_servo_menu_translates_on_the_wire(self):
        menu = servo_menu()
        self.assertEqual(menu._encode_set_target(15, 6824)[:2], bytes((0x84, 13)))   # Throttle fingers on 13.
        port = Port()
        port.reset_input_buffer = lambda: None
        port.replies = {13: 6824}
        self.assertEqual(menu._read_positions(port, [15]), {15: 6824})

    def test_a_servo_whose_pulse_falls_as_it_rises_has_a_flipped_slider(self):
        # Owner, November 24: every up/down slider runs down on the left, up on the right.
        menu = servo_menu()
        visor, elbow = menu._SERVO_DEFAULTS['visor'], menu._SERVO_DEFAULTS['throttle_elbow']
        self.assertEqual(menu._slider('visor', visor, visor['min']), visor['max'])   # Far left: the visor's down pulse.
        self.assertEqual(menu._slider('visor', visor, menu._slider('visor', visor, 5000)), 5000)
        self.assertEqual(menu._slider('throttle_elbow', elbow, 3000), 3000)


class MenuGentleMoveTests(unittest.TestCase):
    """Owner, November 23: slider moves jerked; every one is now gentle, and a limp servo starts where it hangs."""

    def setUp(self):
        self.menu = servo_menu()
        self.port = Port()
        self.port.reset_input_buffer = lambda: None
        self.cfg = dict(self.menu._SERVO_DEFAULTS['neck_turn'])   # The neck has no speed of its own.

    def sent(self):
        return [packet for packet in self.port.written if packet[0] != 0x90]

    def test_a_live_servo_eases_to_the_slider(self):
        self.port.replies = {3: 5000}   # The neck (id 0) is on channel 3, holding 5000.
        self.menu._write_target(self.port, self.cfg, 6000)
        self.assertEqual(self.sent(), [bytes((0x89, 3, 4, 0)), bytes((0x87, 3, 20, 0)),
                                       bytes((0x84, 3, 6000 & 127, 6000 >> 7))])

    def test_a_limp_servo_starts_where_it_hangs_then_eases(self):
        self.port.replies = {3: 0}   # No pulse yet.
        self.menu._write_target(self.port, self.cfg, 7000)
        hang = dict(UNPOWERED_REST)[0]
        self.assertEqual(self.sent()[2:], [bytes((0x84, 3, hang & 127, hang >> 7)), bytes((0x84, 3, 7000 & 127, 7000 >> 7))])
        self.assertEqual(self.menu._limp_start(0, self.cfg), hang)

    def test_a_servo_with_its_own_pace_keeps_it(self):
        self.port.replies = {9: 6000}
        self.menu._write_target(self.port, dict(self.menu._SERVO_DEFAULTS['throttle_shoulder']), 6100)
        self.assertEqual(self.sent()[:2], [bytes((0x89, 9, 6, 0)), bytes((0x87, 9, 30, 0))])


class MenuParkTests(unittest.TestCase):
    """Owner, November 24: one menu item parks every servo, slowly and in clearance-safe stages."""

    def test_park_moves_the_body_then_the_poker_arm_then_the_throttle_and_ends_at_rest(self):
        menu = servo_menu()
        wiring = servo_wiring.load()
        on_board = {channel: servo for servo, channel in wiring.items()}
        pose = {ch: 0 for ch in LIMITS}   # Everything limp.

        class Board(Port):
            def read(self, count):
                last = self.written[-1]
                if last[0] == 0x93:
                    return bytes((0,))   # Never moving: each stage arrives at once.
                return pose[on_board[last[1]]].to_bytes(2, 'little')

            def write(self, packet):
                if packet[0] == 0x84:
                    pose[on_board[packet[1]]] = packet[2] | packet[3] << 7
                return super().write(packet)

        board = Board()
        board.reset_input_buffer = lambda: None
        by_channel = {cfg['ch']: cfg for cfg in menu._SERVO_DEFAULTS.values()}
        self.assertEqual(menu._run_park(board, by_channel), dict(UNPOWERED_REST))
        moved = [on_board[packet[1]] for packet in board.written if packet[0] == 0x84]
        last = {servo: index for index, servo in enumerate(moved)}
        self.assertLess(last[3], min(last[12], last[13]))              # The visor before the poker arm...
        self.assertLess(max(last[12], last[13]), min(last[8], last[9], last[10]))   # ...and the poker arm before the throttle.
        speeds = [packet for packet in board.written if packet[0] == 0x87 and on_board[packet[1]] == 0]
        self.assertEqual(speeds[-1][2], menu._GENTLE_SPEED)            # The neck went at the gentle pace.


if __name__ == '__main__':
    unittest.main()
