"""Dancing (owner, November 25): "dance" is half a minute of the whole body, a song from the Music app is
danced to while it plays, "stop" or "stop dancing" ends it, and "wiggle your fingers" moves only the hands.

Fake serial, fake clock, fake lights and scripted model calls only. Nothing here moves the robot, and
whether the reply model picks dance, arm_dance or finger_wiggle from the descriptions is a paid probe's
job, not this file's."""
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.dance import DANCE_SECONDS, DancePhrase, beat_seconds, dance_lanes
from r3x.music import RS, US, songs
from r3x.output import OutputOwner
from r3x.robot_servos import RobotServos
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import HEAD, LIMITS, POKER, REST, THROTTLE
from r3x.servos import ServoRequest
from r3x.session import Session
from tests.test_memory_core import MemoryFixture
from tests.test_servos import Clock, PulsePort

ARMS = (4, 5, 6, *THROTTLE, *POKER)


def sent(port, since=0):
    """(channel, pulse) for every target sent to the board since packet `since`."""
    out = []
    for p in port.packets[since:]:
        if p[0] == 0x84:
            out.append((p[1], p[2] + 128 * p[3]))
        elif p[0] == 0x9F:
            out.extend((p[2] + i, p[3 + 2 * i] + 128 * p[4 + 2 * i]) for i in range(p[1]))
    return out


class Lights:
    def __init__(self):
        self.calls = []

    def react(self, pattern):
        self.calls.append(('react', pattern))
        return len(self.calls)

    def finish(self, token):
        self.calls.append(('finish', token))

    def settle(self):
        self.calls.append(('settle',))


class Scripted:
    def __init__(self, call):
        self.call = call

    def reply(self, snapshot, cancel):
        yield self.call


class DanceRobot(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start();self.robot.open_eyes()
        self.addCleanup(self.robot.close)
        self.lights = self.robot.dance.lights = Lights()

    def frames(self, seconds, allow=True):
        """Runtime frames for this long; each dance phrase is let run to its end (the fake clock moves as
        the board is waited on)."""
        end = self.clock() + seconds
        while self.clock() < end:
            self.robot.before_frame(allow_idle=allow)
            thread = self.robot.dance._thread
            if thread is not None:
                thread.join(5.)
            self.clock.wait(.05)


class DancePhraseTests(DanceRobot):
    def test_phrases_at_every_tempo_keep_the_arms_safe_and_the_microphone_arm_still(self):
        for bpm in (0, 60, 92, 128, 175):
            for seed in range(12):
                since = len(self.port.packets)
                receipt = self.robot.owner.execute(ServoRequest(f'd{bpm}:{seed}', self.robot.owner.version, 'dance',
                    self.clock() + 60, dance=DancePhrase(beat_seconds(bpm), 8, seed)), Cancellation())
                self.assertEqual(receipt.status, 'pulses_reached', (bpm, seed, receipt.reason))
                self.assertNotIn(7, receipt.changed_channels)
                self.assertTrue(set(HEAD) & set(receipt.changed_channels))
                self.assertTrue(set(ARMS) & set(receipt.changed_channels))
                self.assertTrue(all(v <= dict(REST)[8] for ch, v in sent(self.port, since) if ch == 8))

    def test_tempo_is_the_songs_bpm_folded_into_a_dancing_range_else_its_genre(self):
        self.assertAlmostEqual(beat_seconds(120), .5)
        self.assertAlmostEqual(beat_seconds(60), .5)           # Doubled: 60 looks asleep.
        self.assertAlmostEqual(beat_seconds(174), 60 / 87)     # Halved: the servos cannot swing at 174.
        self.assertAlmostEqual(beat_seconds(0, 'Hip-Hop/Rap'), 60 / 92)
        self.assertAlmostEqual(beat_seconds(None, 'Polka'), 60 / 112)

    def test_held_arms_are_left_out(self):
        current = dict(self.robot.owner.commanded)
        lanes = dance_lanes(current, DancePhrase(.5, 8, 1), held=set(THROTTLE) | set(HEAD))
        moved = {ch for lane in lanes for beat in lane for ch, _ in beat.targets}
        self.assertFalse(moved & (set(THROTTLE) | set(HEAD)))
        self.assertTrue({4, 5, 6, *POKER} <= moved)

    def test_the_songs_bpm_is_read_from_the_music_app(self):
        columns = [['id1'], ['Get Lucky'], ['Daft Punk'], ['RAM'], ['2013'], ['Dance'], ['369'], ['1'], ['8'], ['116']]
        self.assertEqual(songs(RS.join(US.join(c) for c in columns))[0].bpm, 116)
        self.assertEqual(songs(RS.join(US.join(c) for c in columns[:9]))[0].bpm, 0)   # Older nine columns.


class AskedDanceTests(DanceRobot):
    def test_asked_to_dance_he_dances_thirty_seconds_then_every_arm_is_back(self):
        self.robot.idle.tick = lambda *a, **k: None   # Idle movement, which takes over afterwards, is not this test's.
        before = {ch: self.port.pose[ch] for ch in ARMS}
        receipt = self.robot.execute_pose('dance', Cancellation(), 'd')
        self.assertEqual((receipt.status, receipt.reason), ('pulses_reached', 'DanceStarted'))
        began = self.clock()
        self.frames(10)
        self.assertTrue(self.robot.dance.state()['dancing'])
        self.assertNotEqual({ch: self.port.pose[ch] for ch in ARMS}, before)
        while self.robot.dance.state()['dancing'] and self.clock() - began < DANCE_SECONDS + 20:
            self.frames(.5)
        self.frames(1)                               # The last phrase's way home.
        self.assertFalse(self.robot.dance.state()['dancing'])
        self.assertLess(abs(self.clock() - began - DANCE_SECONDS), 4)
        self.assertEqual({ch: self.port.pose[ch] for ch in ARMS}, before)
        reacts = [c for c in self.lights.calls if c[0] == 'react']
        self.assertGreater(len({c[1] for c in reacts}), 2)                  # A new colour every bar or two.
        self.assertEqual(self.lights.calls[-1], ('settle',))               # The lights are plain again.
        self.assertFalse(self.robot.owner.held_channels - self.robot.owner.kept_channels)

    def test_a_person_speaking_pauses_it_and_it_carries_on(self):
        self.robot.execute_pose('dance', Cancellation(), 'd')
        self.frames(3)
        self.robot.note_speech()
        packets = len(self.port.packets)
        self.frames(2, allow=False)
        self.assertFalse(sent(self.port, packets))                         # Still while they talk.
        self.assertTrue(self.robot.dance.wanted())
        self.frames(2)
        self.assertTrue(sent(self.port, packets))

    def test_his_own_goal_cannot_start_a_dance_and_stop_dancing_ends_one(self):
        self.assertEqual(self.robot.execute_pose('dance', Cancellation(), 'own', hold_pose=False).reason,
                         'DanceOnlyWhenAsked')
        self.robot.execute_pose('dance', Cancellation(), 'd')
        self.frames(2)
        self.assertEqual(self.robot.execute_pose('stop_dancing', Cancellation(), 's').reason, 'DanceStopped')
        self.assertFalse(self.robot.dance.wanted())
        self.assertEqual(self.robot.execute_pose('stop_dancing', Cancellation(), 's2').reason, 'WasNotDancing')


class MusicDanceTests(DanceRobot):
    def test_he_dances_while_a_song_plays_and_stop_dancing_lasts_for_that_song(self):
        self.robot.dance.music(True, .5, 'song1')
        self.frames(3)
        self.assertEqual(self.robot.dance.state(), {'dancing': True, 'to_music': True})
        self.robot.execute_pose('stop_dancing', Cancellation(), 's')
        self.robot.dance.music(False)                           # Paused for his line...
        self.robot.dance.music(True, .5, 'song1')               # ...and back: still not dancing to it.
        self.assertFalse(self.robot.dance.wanted())
        self.assertEqual(self.robot.dance.state(), {'dancing': False, 'stopped_for_this_song': True})
        self.robot.dance.music(True, .55, 'song2')              # The next song is danced to.
        self.assertTrue(self.robot.dance.wanted())
        self.robot.dance.music(False)
        self.assertFalse(self.robot.dance.wanted())


class FingerWiggleTests(DanceRobot):
    def test_wiggle_moves_only_the_hands_back_and_forth_and_returns_them(self):
        before = dict(self.port.pose)
        since = len(self.port.packets)
        receipt = self.robot.execute_pose('finger_wiggle', Cancellation(), 'w')
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertEqual(set(receipt.changed_channels), {5, 14, 15, 16, 17})
        self.assertEqual(dict(self.port.pose), before)
        fingers = [v for ch, v in sent(self.port, since) if ch == 15]
        self.assertGreaterEqual(len(set(fingers)), 2)                      # Open and closed, back and forth.
        self.assertTrue(all(LIMITS[15][0] <= v <= LIMITS[15][1] for v in fingers))
        self.assertFalse(self.robot.owner.held_channels - self.robot.owner.kept_channels)


class DanceSessionTests(DanceRobot):
    def session(self, call):
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=self.robot)
        self.state.trace.clock = self.clock
        session = Session('s', Scripted(call), output, self.state.trace, self.state)
        self.addCleanup(session.close)
        return session

    def test_dance_for_us_starts_it_and_stop_ends_it(self):
        session = self.session(ToolCall('p', 'set_robot_pose', {'poses': ['dance'], 'say_before': 'Hit it!',
                                                                'look_after': None, 'text': None}))
        result = session.process_turn(Capture(uuid4().hex, 's', bytes(1024), 98., 99., 99.6),
                                      Transcript('Rex, dance for us!', -.01, True), session.revision)
        self.assertEqual(result.completed_text, ['Hit it!'])
        self.assertTrue(self.robot.dance.state()['dancing'])
        session.cancel('explicit_stop')
        self.assertFalse(self.robot.dance.wanted())
