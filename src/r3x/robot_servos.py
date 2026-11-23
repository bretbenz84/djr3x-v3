"""Explicit robot lifecycle, with park before port release."""
from dataclasses import asdict
import threading
import time
from .adapters.maestro import open_serial
from .contracts import Cancellation, ProviderFailure
from .providers import Capability
from .servo_poses import (throttle_extends, ARM_HOLD, HAND_PARK, HAND_STEPS, HEAD, LIMITS, THROTTLE, THROTTLE_HAND, REACHING_ARMS, REST, SENTRY_ARM_OUT, SENTRY_STANCE, TELEPRESENCE_DRIVE_HEAD,
                          pose_channels, sentry_head, telepresence_targets, visor_over_eyes)
from .servos import ServoOwner, ServoReceipt, ServoRequest, TalkingMotion
from .idle_motion import IdleMotion
from .voice_reflex import VoiceReflex


# A picture asked for after a drive has a 3 s capture window from 0.3 s after the stop.
LOOK_STILL_S = 4.
# Speech heard this recently means the person is still talking: no listening cue now.
STILL_TALKING_S = .3
# How long a request waits for a move the hand is making by itself (its longest, the walk back to rest
# from the floor, is about 5 s).
HAND_YIELD_S = 12.


class RobotServos:
    capability = Capability('robot_servos','Pololu Maestro','startup, idle arms, talking, microphone hold and park',
        dependencies=('pyserial',),resources=('exclusive Maestro; eighteen calibrated channels',),
        shutdown='join idle/talking; park; close port; acknowledge ownership release')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=time.sleep, telepresence=False,
                 arm_guard=False):
        self.owner = ServoOwner(open_port or (lambda:open_serial(port)),medium='controller',clock=clock)
        self.idle = IdleMotion(self.owner)
        self.talking = TalkingMotion(self.owner)
        self.reflex = VoiceReflex(self.owner)
        self.reflex.on_voice = self.idle.look_at_people   # Someone is talking: look at them again.
        def quiet():
            self.idle.stop()
            self.reflex.stop()
        self.talking.before_start = quiet
        self.talking.after_speech = self.idle.after_speech
        # Idle never starts a phrase while talking motion or the hand's own move needs the servos
        # (run 471f4739: the arm's return after a let go lost the board to idle every frame for a minute).
        self.idle.busy = lambda: self.talking._thread is not None or self._hand_busy()
        self.trace = None
        self.wait = wait
        self.closed = False
        self.started = False
        self.awake = False   # The wake has finished. Until then the drive owner starts no move of its own (app).
        self.before_shutdown = lambda: None
        self.on_startup = lambda: None   # Startup sounds begin with the wake choreography.
        self.on_shutdown = lambda: None  # The power-down sound begins with the park choreography.
        self._arm_return = None
        self._speech_at = float('-inf')
        self.telepresence = telepresence   # A telepresence call: start runs its short wake instead.
        self.telepresent = False   # The call's wake reached its pose; only the caller moves him now.
        # Owner, November 17: at the wake the poker arm came out into something beside him. With a
        # drive base (its range sensors) the wake leaves the reaching arms where they are; the drive
        # owner brings each out once its space reads clear (DriveBase.tick_arm_clearance), as after a drive.
        # Owner, November 21: "if there's computed clearance", the arms should stir while he wakes. The
        # wheel board now starts beside the wake, so an arm whose space has read clear comes out during it.
        self.arm_guard = arm_guard
        # The drive owner's range reading, when he has wheels: a fully extended throttle pose needs
        # its front-right space clear (owner, November 20).
        self.arm_space = None
        # Holding something light (owner, November 20): the widths in pixels of the faces in view now,
        # when he has a camera, and the wait while he holds it out to give back (hand_hold.HandWatch).
        self.hand_faces = None
        self._hand = None
        # 'empty', 'taking' (reaching out and turning to look), 'open' (waiting for it), 'holding',
        # 'offering' (held out to give back), 'letting_go'.
        self._hand_state = 'empty'
        self._let_go_at = None
        self._hand_worker = None
        self._hand_superseded = threading.Event()   # A request has taken the arm from the hand's own next move.
        self._opened_at = None          # When the arm was out with the hand open: it closes CLOSE_AFTER_S later.
        # It closed on that time, not on their word: told they missed it, hand_take opens it again.
        self._closed_on_time = False
        self._hand_moves = 0
        # (read, write) for the remembered hold (hand_hold.held/set_held), when the app has its database.
        self.held_memory = None
        self._hand_reported_at = float('-inf')

    def _sequence(self, kind, arms_home=False, keep_arms=frozenset()):
        result = self.owner.execute(ServoRequest(kind,self.owner.version,kind,self.owner.clock()+120.,
                                                 arms_home=arms_home, keep_arms=keep_arms),Cancellation())
        if self.trace:
            self.trace.emit('servo_lifecycle',kind=kind,**asdict(result))
        if result.status != 'pulses_reached':
            raise ProviderFailure('Servo'+kind.title()+':'+(result.reason or result.status))

    def start(self):
        if self._remembered_hold():
            # Still holding something from the last run: the board's first pulses, the wake and idle
            # keep the fingers closed and the arm where it is (owner, November 20).
            self.owner.grip_channels = frozenset((*THROTTLE, *THROTTLE_HAND))
            self._hand_state = 'holding'
        self.owner.start()
        self.started = True
        if self.trace and self.owner.energized_channels:
            self.trace.emit('servo_unpowered_rest', channels=list(self.owner.energized_channels))
        try:
            self.on_startup()
        except Exception as exc:
            if self.trace: self.trace.emit('servo_startup_hook_failed',error_type=type(exc).__name__)
        if self.telepresence:
            self._telepresence_wake()
            self.awake = True
            return
        self._sequence('startup', keep_arms=frozenset(REACHING_ARMS) if self.arm_guard else frozenset())
        if self.trace and self.owner.kept_arms:
            self.trace.emit('arm_clearance', status='kept_parked', reason='startup', arms=sorted(self.owner.kept_arms))
        if self.owner.grip_channels:
            self.owner.held_channels.update(self.owner.grip_channels)
            if self.trace:
                self.trace.emit('hand_hold', status='holding', reason='remembered_at_startup')
        # Owner, November 15: the wake leaves the visor shut over his eyes, and it stays shut (talking
        # and idle motion leave it alone) until everything has loaded (open_eyes).
        self.owner.held_channels.add(3)
        self.awake = True
        # The wake runs beside the rest of startup and ends just before the chime (owner, November 21:
        # it was over four seconds early), so nothing waits here for the array arm's hold to settle:
        # the hold dates itself from when the arm last moved, and nothing heard before the ready line
        # is kept (AudioCapture.discard_pending).
        self.owner.refresh_microphone_hold()

    def eyes_may_open(self):
        """The startup hold on the visor ends: talking and idle motion may move it again."""
        self.owner.held_channels.discard(3)

    def open_eyes(self):
        """Everything has loaded: the visor goes up to its usual open point, just before he says he
        is ready (owner, November 15). Should the move be refused, the hold is still gone, so the
        ready line's talking motion opens it and he is never left awake and blind."""
        self.eyes_may_open()
        if self.owner.state != 'active' or not visor_over_eyes(self.owner.commanded):
            return None
        receipt = self.owner.execute(ServoRequest('startup-open-eyes', self.owner.version, 'pose',
                                                  self.owner.clock()+60, pose='visor_normal', hold_pose=False), Cancellation())
        if self.trace:
            self.trace.emit('servo_lifecycle', kind='open_eyes', **asdict(receipt))
        return receipt

    def cancel(self):
        self.idle.close()
        self.reflex.close()
        self.talking.cancel()
        self.owner.cancel()

    def begin_operator(self):
        """Dashboard manual override: Rex's own servo motion (idle, talking, reflexes, the
        microphone recentre, gestures and poses) yields until end_operator. The flag is set
        first, so nothing new starts while the running workers are stopped; a request already
        on the wire finishes (operator poses meanwhile get ServoBusy)."""
        if self.owner.state != 'active':
            raise ProviderFailure('ServoUnavailableOrParked')
        if self._arm_return is not None and self._arm_return.is_alive():
            raise ProviderFailure('ArmReturnStillActive')
        self.owner.operator = True
        try:
            self.idle.stop()
            self.reflex.stop()
            self.talking.stop()
        finally:
            if self.trace:
                self.trace.emit('operator_servos', status='started')

    def operator_pose(self, pose, cancel, request_id):
        """One manual pose: calibrated limits, pair routes and clearance checks still apply."""
        return self.owner.execute(ServoRequest(request_id, self.owner.version, 'operator',
                                               self.owner.clock() + 15., pose=dict(pose)), cancel)

    def end_operator(self):
        if not self.owner.operator:
            return
        self.owner.operator = False
        if self.trace:
            self.trace.emit('operator_servos', status='ended', commanded=dict(self.owner.commanded))
        self.idle.look_at_people()
        self.idle.wake()

    def before_frame(self, *, allow_idle=False, capturing=False, calm=False):
        self.idle.trace = self.reflex.trace = self.trace
        if self.owner.release_expired_look():
            self.idle.look_at_people()   # A person's look is over: the head follows people again.
        if self.owner.operator:
            self.idle.tick(False)
            return
        self._tick_hand()
        self.idle.tick(allow_idle and self.talking._thread is None, calm)
        if (not capturing and self.talking._thread is None and self.idle._thread is None and
                not (self.reflex._thread and self.reflex._thread.is_alive())):
            self.owner.refresh_microphone_hold()

    def execute_pose(self, name, cancel, request_id, *, hold_pose=True, beside_visor=False):
        self.idle.stop()
        self.reflex.stop()   # The model's explicit move replaces an unfinished reflex.
        if not (beside_visor and self.talking.visor_only):
            self.talking.stop()
        if name == 'hold_arms':
            # Nothing moves and nothing is heard, so there is nothing for newer speech to
            # interrupt: October 3, "keep your hand down and don't move it" was understood
            # and then cancelled 0.2 s later by someone else talking.
            cancel = Cancellation()
        follow, requested = name == 'look_at_people', hold_pose
        if follow:
            # Following faces is the idle gaze's job, never a held pose. When a person asks
            # for it, their earlier held look gives way; Rex's own cannot override a hold.
            if hold_pose:
                self.owner.held_channels.difference_update(HEAD)
            elif self.owner.holding_requested_look():
                # Rex's own look back does not end a look a person asked for (a goal beat's
                # look_at_people while he searches the table for them).
                return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(),
                                    self.owner.medium, reason='HoldingRequestedLook')
            hold_pose = False
            try:
                aim = self.idle.face() if self.idle.face else None
            except Exception:
                aim = None
            if aim:
                name = {0: max(LIMITS[0][0], min(LIMITS[0][1], int(aim['neck'])))}
        if self._takes_arm(name):
            self._hand_gives_way()
        if name == 'hand_let_go' and self._hand_state == 'holding':
            return self._give_back(request_id)   # Out to the grab position first; it opens once someone is close.
        if name == 'hand_put_down':
            return self._put_down(request_id)
        if name == 'hand_take' and (self._hand_state == 'offering'
                                    or (self._hand_state == 'holding' and not self._closed_on_time)):
            # Opening to take another would drop what he holds. Closed by the three seconds alone, it may hold
            # nothing: "I didn't make it" opens it to try again (owner, November 20).
            return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                reason='AlreadyHoldingSomething')
        if name == 'hand_grip' and self._hand_state == 'empty' and not self._at_grab():
            # Review, November 20: after a wait had run out and the arm had gone back to rest, "there you go"
            # closed the fingers on nothing, and he remembered holding it across launches.
            return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                reason='HandNotOut')
        # The grab position (elbow 1720 us) is not a full extension, but holding something still needs
        # the room in front of the hand (owner, November 20: "if there's clearance").
        if (name == 'hand_take' and not self._at_grab()) or throttle_extends(name, self.owner.commanded):
            # Already out at the grab position, the reading in front is his own arm or the person handing it over.
            blocked = self._reach_blocked(request_id, hand=name == 'hand_take')
            if blocked is not None:
                return blocked
        def run():
            return self.owner.execute(ServoRequest(request_id,self.owner.version,'pose',
                self.owner.clock()+60,pose=name,hold_pose=hold_pose),cancel)
        receipt, patience = run(), self.owner.clock() + HAND_YIELD_S
        while (receipt.reason == 'ServoBusy' and self._hand_busy() and not cancel.event.is_set()
               and threading.current_thread() is not self._hand_worker and self.owner.clock() < patience):
            # The hand is in the middle of a move of its own (folding in, going back to rest): the request
            # waits for the board instead of being refused (review, November 20: "I couldn't complete that
            # movement" for a "hold this" two seconds after a let go).
            self.wait(.05)
            receipt = run()
        if receipt.status == 'pulses_reached':
            if follow or name == 'release_poses':
                if requested:
                    self.owner.end_requested_look()   # They asked him to look back: the search is over.
                self.idle.look_at_people()
            else:
                if requested:
                    self.owner.hold_look(pose_channels(name))   # A person's head pose lasts as their look does.
                self.idle.posed(receipt.changed_channels or pose_channels(name), own=not hold_pose)
            if isinstance(name, str) and name in HAND_STEPS:
                self._hand_step(name, request_id)
            elif (self._hand_state in ('open', 'letting_go', 'offering') and
                  (name == 'release_poses' or set(receipt.changed_channels or pose_channels(name)) & set(THROTTLE))):
                offering = self._hand_state == 'offering'
                self._hand_reset('arm_moved')   # Another arm pose: no closing on, or resting from, a wait.
                if offering and name == 'release_poses':
                    # Told to relax while holding it out: release_poses moves nothing he holds, so the arm
                    # folds back in with it instead of staying out for good (review, November 20).
                    self._fold_held(request_id)
        return receipt

    def _takes_arm(self, name) -> bool:
        """Whether this pose takes the throttle arm or hand over from a move the hand was about to make."""
        if isinstance(name, str) and (name in HAND_STEPS or name in ('release_poses', 'hold_arms')):
            return True
        return bool(set(pose_channels(name)) & {*THROTTLE, *THROTTLE_HAND})

    def _hand_gives_way(self):
        """A person's hand or arm request, or a drive, while the hand makes a move of its own (review,
        November 20). A move under way finishes first; one still waiting for the board is dropped, so it
        cannot run afterwards and undo the request (a waiting return to rest pulled the arm back in after
        a new "hold this" had reached it out)."""
        worker = self._hand_worker
        if worker is None or worker is threading.current_thread() or not worker.is_alive():
            return
        self._hand_superseded.set()
        try:
            worker.join(HAND_YIELD_S)
        finally:
            self._hand_superseded.clear()

    def _reach_blocked(self, request_id, hand=False):
        """A refusal when the throttle may not reach out: its front-right space is not clear (owner, November 20).
        hand: out to the grab position to take or give back, which is not a full extension and needs only the room
        that lets the arm out at all (run b6249519: 0.48 m with Bret three feet away refused "let go" twice; the
        sensor read 0.52 m with nobody near in run 17e088f3)."""
        if self.arm_space is None:
            return None
        from .drive_space import THROTTLE_EXTEND_CLEAR_M, THROTTLE_KEEP_CLEAR_M, throttle_front_m
        try:
            ahead = throttle_front_m(self.arm_space())
        except Exception:
            ahead = None
        if ahead is not None and ahead >= (THROTTLE_KEEP_CLEAR_M if hand else THROTTLE_EXTEND_CLEAR_M):
            return None
        return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                            reason='ThrottleExtensionBlocked:' + ('front_right_unknown' if ahead is None
                                                                  else f'front_right_{ahead:.2f}m'))

    # Holding something light (owner, November 20) --------------------------------------------
    # empty -> open (reached out, fingers open, waiting) -> holding (fingers closed, arm folded at its
    # drive park) -> offering (asked for it back: out at the grab position, still closed) -> letting_go
    # (fingers open) -> empty (the arm at rest). Moves he makes by himself run in a worker thread.
    def hand_state(self) -> str:
        return self._hand_state

    def hand_closed_on_time(self) -> bool:
        """Holding because the open hand's three seconds ran out, not because a person said it was in."""
        return self._hand_state == 'holding' and self._closed_on_time

    def _hand_busy(self) -> bool:
        return self._hand_worker is not None and self._hand_worker.is_alive()

    def _at_grab(self) -> bool:
        from .servo_poses import THROTTLE_GRAB
        return all(abs(self.owner.commanded.get(ch, -10000) - v) <= 2 for ch, v in THROTTLE_GRAB)

    def _hand_execute(self, request_id, pose, patience=20.):
        """One of the hand's own moves, waiting its turn while talking motion, a reflex or a listening cue
        has the board (run 471f4739: a single try lost to them every frame). Held like a person's pose
        while it runs, so nothing else moves those joints, without restarting the person-pose clock."""
        deadline = self.owner.clock() + patience
        held_at = self.owner.pose_held_at
        while True:
            if self._hand_superseded.is_set() and threading.current_thread() is self._hand_worker:
                self.owner.pose_held_at = held_at
                return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                    reason='HandMoveSuperseded')
            receipt = self.owner.execute(ServoRequest(request_id, self.owner.version, 'pose', self.owner.clock() + 60.,
                                                      pose=dict(pose)), Cancellation())
            if receipt.reason != 'ServoBusy' or self.owner.clock() >= deadline or self.closed:
                self.owner.pose_held_at = held_at
                return receipt
            self.wait(.05)

    def _remembered_hold(self) -> bool:
        if self.held_memory is None:
            return False
        try:
            return bool(self.held_memory[0]())
        except Exception as exc:
            if self.trace:
                self.trace.emit('hand_hold', status='memory_read_failed', error_type=type(exc).__name__)
            return False

    def _remember_hold(self, holding, why):
        if self.held_memory is None:
            return
        try:
            self.held_memory[1](holding, why)
        except Exception as exc:
            if self.trace:
                self.trace.emit('hand_hold', status='memory_write_failed', error_type=type(exc).__name__)

    def _look_at_hand(self, request_id):
        """Down and to his right, at the hand, held while he waits."""
        from .hand_hold import WAIT_S   # The longest wait (giving back); the grip turns his eyes back sooner.
        for step in ('look_right', 'look_down'):
            look = self.execute_pose(step, Cancellation(), f'{request_id}:{step}')
            if look.status != 'pulses_reached':
                if self.trace:
                    self.trace.emit('hand_hold', status='look_failed', step=step, reason=look.reason)
                break
        self.owner.hold_look(HEAD, WAIT_S)

    def _eyes_back(self):
        self.owner.end_requested_look()
        self.idle.look_at_people()

    def _hand_step(self, name, request_id):
        """A hand step's pulses arrived: what the hand is doing now."""
        if name == 'hand_take':
            out_at = self.owner.clock()   # The arm is out: the countdown starts now, not after the look or the line.
            if self._hand_state == 'holding':   # Closed on time and they missed it: it held nothing.
                self._remember_hold(False, 'try_again')
            self.owner.grip_channels = frozenset()
            # Nothing per frame acts on the hand while he turns to look at it (run 471f4739 crashed here).
            self._hand, self._hand_state, self._let_go_at, self._opened_at = None, 'taking', None, None
            self._closed_on_time = False
            self._look_at_hand(request_id)
            self._opened_at = out_at
            self._hand_state = 'open'
        elif name == 'hand_grip':
            # The hand's own close (ids hand:) is the three seconds running out; a person's is their word.
            self._closed_on_time = request_id.startswith('hand:') and self._hand_state == 'open'
            self._hand, self._hand_state, self._opened_at = None, 'holding', None
            self.owner.grip_channels = frozenset((*THROTTLE, *THROTTLE_HAND))
            self.owner.held_channels.update(self.owner.grip_channels)
            self._remember_hold(True, 'gripped')
            # The pulse arrives before the fingers have finished closing on it: they get a moment to grip
            # before the arm moves (run fe1d4fea: the arm folded as the fingers were still closing).
            from .hand_hold import GRIP_SETTLE_S
            self.wait(GRIP_SETTLE_S)
            self._eyes_back()   # Got it: his eyes go back to the people,
            self._fold_held(request_id)   # and the arm folds in to its drive park with it.
        else:   # hand_let_go
            if self._hand_state in ('open', 'offering'):
                self._eyes_back()
            self._hand, self._hand_state, self._opened_at, self._closed_on_time = None, 'letting_go', None, False
            self.owner.grip_channels = frozenset()
            self._let_go_at = self.owner.clock()
            self._remember_hold(False, 'let_go')
        if self.trace:
            self.trace.emit('hand_hold', status=self._hand_state, step=name, request_id=request_id)

    def _fold_held(self, request_id):
        """Holding something, the throttle arm folds in to the drive stance's park; the fingers stay closed."""
        from .servo_poses import PARK
        receipt = self._hand_execute(f'hand:fold:{request_id}', PARK)
        if self.trace:
            self.trace.emit('hand_hold', status='folded' if receipt.status == 'pulses_reached' else 'fold_failed',
                            reason=receipt.reason)
        return receipt

    def _give_back(self, request_id):
        """Asked to give it back or let go while holding it: out to the grab position, fingers still closed,
        looking at the hand; they open once someone comes close (owner, November 20)."""
        from .hand_hold import HandWatch
        from .servo_poses import THROTTLE_GRAB
        blocked = self._reach_blocked(request_id, hand=True)
        if blocked is not None:
            return blocked
        receipt = self._hand_execute(f'hand:offer:{request_id}', THROTTLE_GRAB)
        if receipt.status == 'pulses_reached':
            self._look_at_hand(request_id)
            self._hand, self._hand_state = HandWatch(self.owner.clock()), 'offering'
        if self.trace:
            self.trace.emit('hand_hold', status=self._hand_state, step='hand_let_go', request_id=request_id,
                            reason=receipt.reason)
        return receipt

    def _put_down(self, request_id):
        """Put what he holds on the floor (owner, November 20): the arm lowers all the way (throttle_down's
        pose, by way of a raised arm, so the front right must be clear), then the fingers open and the
        arm rests a moment later."""
        from .servo_poses import FINGERS_OPEN, FULL_DOWN
        if self._hand_state not in ('holding', 'offering'):
            return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                reason='NotHoldingAnything')
        blocked = self._reach_blocked(request_id)
        if blocked is not None:
            return blocked
        if self._hand_state == 'offering':
            self._hand, self._hand_state = None, 'holding'
            self._eyes_back()
        lowered = self._hand_execute(f'hand:down:{request_id}', FULL_DOWN)
        if self.trace:
            self.trace.emit('hand_hold', status='lowered' if lowered.status == 'pulses_reached' else 'lower_failed',
                            reason=lowered.reason, request_id=request_id)
        if lowered.status != 'pulses_reached':
            return lowered
        opened = self._hand_execute(f'hand:release:{request_id}', {15: FINGERS_OPEN})
        if opened.status == 'pulses_reached':
            self._hand_step('hand_let_go', request_id)
        return opened

    def _hand_reset(self, why):
        """Another arm pose or a drive ends a wait. What he holds stays held."""
        if self._hand_state in ('open', 'offering'):
            self._eyes_back()
        self._hand, self._let_go_at, self._opened_at = None, None, None
        if self._hand_state == 'offering':
            self._hand_state = 'holding'
        else:
            self._hand_state, self._closed_on_time = 'empty', False
            self.owner.grip_channels = frozenset()
        if self.trace:
            self.trace.emit('hand_hold', status=self._hand_state, reason=why)

    def _tick_hand(self):
        """Each frame: close the open hand once its three seconds are up, open the offered one when someone
        comes close, or rest or fold the arm after a wait or a let go. Never raises into the frame loop
        (run 471f4739: a TypeError here stopped the runtime and shut him down)."""
        try:
            self._tick_hand_now()
        except Exception as exc:
            if self.trace:
                self.trace.emit('hand_hold', status='tick_failed', error_type=type(exc).__name__, state=self._hand_state)

    def _tick_hand_now(self):
        from .hand_hold import CLOSE_AFTER_S, LET_GO_S
        if (self._hand_state not in ('open', 'offering', 'letting_go')
                or (self._hand_worker and self._hand_worker.is_alive())):
            return
        now = self.owner.clock()
        if self._hand_state == 'letting_go':
            if self._let_go_at is not None and now - self._let_go_at >= LET_GO_S:
                self._hand_work(self._rest_hand, 'let_go')
            return
        if self._hand_state == 'open':
            # Owner, November 20: "ok, ready", then the hand closes three seconds later, nothing more said.
            if self._opened_at is not None and now - self._opened_at >= CLOSE_AFTER_S:
                if self.trace:
                    self.trace.emit('hand_hold', status='closing', after_s=round(now - self._opened_at, 2))
                self._hand_work(self._hand_move, 'hand_grip')
            return
        front = face = None
        if self.arm_space is not None:
            from .drive_space import throttle_front_m
            try:
                front = throttle_front_m(self.arm_space())
            except Exception:
                front = None
        face_at = None
        if self.hand_faces is not None:
            try:
                face, face_at = max(self.hand_faces(), default=(None, None))   # The widest face and when it was seen.
            except Exception:
                face = face_at = None
        if self._hand is None:
            return
        decision = self._hand.observe(now, front, face, face_at)
        if self.trace and (decision or now - self._hand_reported_at >= 2.):
            # What the wait sees, for tuning (run fe1d4fea gave nothing to go on).
            self._hand_reported_at = now
            self.trace.emit('hand_watch', state=self._hand_state, decision=decision, front_right_m=front,
                            baseline_m=self._hand.baseline_m, face_px=face)
        if decision == 'grip':   # Someone is at the hand to take it back.
            if self.trace:
                self.trace.emit('hand_hold', status='opening', front_right_m=front,
                                face_px=face, baseline_m=self._hand.baseline_m)
            self._hand_work(self._hand_move, 'hand_let_go')
        elif decision == 'timeout':
            self._hand_work(self._keep_holding, 'nobody_came')

    def _hand_work(self, work, *args):
        self._hand_moves += 1
        def run():
            try:
                work(*args)
            except Exception as exc:   # Seen in the trace, never silent; the next frame may try again.
                if self.trace:
                    self.trace.emit('hand_hold', status='move_failed', error_type=type(exc).__name__,
                                    state=self._hand_state)
        self._hand_worker = threading.Thread(target=run, daemon=True, name='throttle-hand')
        self._hand_worker.start()

    def _hand_move(self, step):
        receipt = self.execute_pose(step, Cancellation(), f'hand:{step}:{self._hand_moves}')
        if receipt.status != 'pulses_reached' and self.trace:   # Busy: the next frame tries again.
            self.trace.emit('hand_hold', status='move_failed', step=step, reason=receipt.reason or receipt.status)

    def _keep_holding(self, why):
        """Nobody came for it: the arm folds back in, still holding."""
        self._hand, self._hand_state = None, 'holding'
        self._eyes_back()
        self._fold_held(f'hand:{why}:{self._hand_moves}')

    def _rest_hand(self, why):
        """The arm back to rest and the hand to its park, after letting go. It waits
        for a line's talking motion to finish rather than cutting it off."""
        if self.owner.hold_until_released:   # "Hold your arms there": the arm stays out.
            self._hand_reset(why)
            return
        self.reflex.stop()
        receipt = self._hand_execute(f'hand:rest:{self._hand_moves}', {**dict(REST), **dict(HAND_PARK)})
        if receipt.reason in ('ServoBusy', 'HandMoveSuperseded'):
            if self.trace:
                self.trace.emit('hand_hold', status='rest_waiting' if receipt.reason == 'ServoBusy' else 'rest_dropped',
                                reason=why)
            return   # Still busy after its patience: a later frame tries again. Dropped: the request owns the arm now.
        if self.trace:
            # Run 5aa92add (November 21): passing the receipt's status and reason beside these raised a TypeError,
            # so the hand never left letting_go and every frame sent the arm to rest again.
            self.trace.emit('hand_hold', status='arm_rest', reason=why, move_status=receipt.status,
                            move_reason=receipt.reason)
        if receipt.status == 'pulses_reached':
            self.owner.held_channels.difference_update({*THROTTLE, *THROTTLE_HAND})
            self.idle.posed(receipt.changed_channels, own=True)
        self._hand_reset(why)

    def keep_view(self, seconds):
        """A person asked what he sees where his head already points: it stays there while he answers.
        October 31 (f6d5a0b7): asked what he was looking at, he turned to Bret as he answered "you"."""
        if self.owner.state not in ('active', 'moving'):
            return
        self.idle.stop()
        self.reflex.stop()
        self.talking.stop()
        self.owner.hold_look(HEAD, seconds)

    def keep_requested_look(self, seconds, *, where=None, why=None):
        """A person asked him to look somewhere: the head stays there for this long (the voice
        reflex and the idle gaze leave it), and where it points is kept to look there again."""
        now = self.owner.clock()
        self.owner.look_hold_until = now + seconds
        if where is not None:
            self.owner.requested_look = {'pose': {ch: self.owner.commanded[ch] for ch in HEAD if ch in self.owner.commanded},
                                         'where': where, 'why': (why or '')[:200], 'at': now}

    def drive_look(self, pose, cancel, request_id):
        """A head move the drive owner makes to look at something mid-drive. The drive
        stance already holds the head, and this is nobody's requested pose: it must not
        start the hold that keeps Rex's own movement off a person's pose for 90 seconds.
        A line he is saying as he rolls keeps its visor going: the look moves the head only."""
        held_at, look = self.owner.pose_held_at, (self.owner.look_hold_until, set(self.owner.look_channels))
        try:
            return self.execute_pose(pose, cancel, request_id, beside_visor=True)
        finally:
            self.owner.pose_held_at = held_at
            self.owner.look_hold_until, self.owner.look_channels = look

    def execute_expression(self, request, cancel):
        self.idle.stop()
        self.reflex.stop()
        self.talking.stop()
        cancel.check()
        from dataclasses import replace
        receipt = self.owner.execute(replace(request,version=self.owner.version),cancel)
        if self.trace:self.trace.emit('servo_expression',**asdict(receipt))
        if receipt.status == 'pulses_reached' and request.kind == 'attention':
            # A silent look is the model choosing a direction; looking forward returns to people.
            if request.target == 'forward': self.idle.look_at_people()
            else: self.idle.hold_direction()
        return receipt

    def prepare_base_motion(self, cancel):
        """Park the throttle arm and center the neck before any non-reverse base motion.
        A line said as he sets off keeps its visor moving (the stance leaves the visor alone)."""
        self.owner.end_requested_look()   # The drive centres the head: a held look is over.
        self._hand_gives_way()   # A return to rest still waiting would unfold the arm while he rolls.
        if self._hand_state in ('open', 'offering', 'letting_go'):
            self._hand_reset('drive')   # The stance folds the arm; what he holds stays held.
        self.idle.stop()
        self.reflex.stop()
        if not self.talking.visor_only:
            self.talking.stop()
        if self._arm_return is not None:
            self._arm_return.join(15.)
            if self._arm_return.is_alive():
                return ServoReceipt('base-park', 'rejected', self.owner.version, self.owner.clock(),
                                    self.owner.medium, reason='ArmReturnStillActive')
            self._arm_return = None
        receipt = self.owner.execute(ServoRequest('base-park', self.owner.version, 'base_park',
                                                  self.owner.clock()+30.), cancel)
        if self.trace:
            self.trace.emit('servo_base_park', **asdict(receipt))
        return receipt

    def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
        """The drive is over: the head is free at once, while the arms the stance moved
        return to their awake poses in the background (the throttle to rest, the hero elbow
        and poker arm to their mid points; October 10).

        keep_parked: arms with something within their reach (the drive owner's reading,
        owner October 11); they stay at their drive stance until extend_cleared_arms.

        Owner, October 3: he stayed frozen until the arm had un-parked. Talking and idle
        motion now carry on beside the return (they never command the arm's channels while
        it is on its way). A later drive plan or shutdown joins this worker first.

        look_pending: a picture is about to be taken from where he stopped, so the head
        stays still for it first (the session wakes idle motion once it is taken).
        """
        self._start_arm_return(frozenset(keep_parked))
        self.idle.look_at_people()   # The drive centred the head; an earlier held look is over.
        self.idle.wake(delay=LOOK_STILL_S if look_pending else 0.)   # Now, not after the usual idle pause.

    @property
    def kept_arms(self):
        return self.owner.kept_arms

    def extend_cleared_arms(self, still_blocked):
        """Return the kept arms no longer blocked to their awake poses; the rest stay.
        Nothing starts while an earlier return is still on its way."""
        kept = self.owner.kept_arms
        if (not kept or kept <= still_blocked or self.owner.hold_until_released   # "Hold your arms" stays.
                or self.owner.state not in {'active', 'moving'}
                or (self._arm_return is not None and self._arm_return.is_alive())):
            return False
        self._start_arm_return(kept & frozenset(still_blocked))
        return True

    def _start_arm_return(self, keep):
        import threading
        self.owner.begin_arm_return()
        def run():
            receipt = self.owner.return_arm(keep)
            if self.trace:
                self.trace.emit('servo_base_release', **asdict(receipt), kept_parked=sorted(keep),
                                route=self.owner.arm_return_route)
        self._arm_return = threading.Thread(target=run, name='r3x-arm-return', daemon=True)
        self._arm_return.start()

    # Telepresence (owner, November 16; docs/TELEPRESENCE_PLAN.md) -----------------------
    def _telepresence_wake(self):
        """A call's wake, in place of Rex's own (owner, November 16 evening: his wake choreography
        made a call slow to start, and then the first drive waited for the arms): straight to the
        drive stance with the visor open (servo_poses.telepresence_wake_lanes), held all call, so
        the wheels never wait for an arm. Then the operator override goes on, so Rex's own motion (idle life, talking motion, the listening cue, reflexes,
        gestures and expressions) is refused; only telepresence_pose and telepresence_level move him."""
        receipt = self.owner.execute(ServoRequest('telepresence:wake', self.owner.version, 'telepresence_wake',
                                                  self.owner.clock() + 30.), Cancellation())
        if self.trace:
            self.trace.emit('servo_telepresence', kind='wake', **asdict(receipt))
        if receipt.status != 'pulses_reached':
            raise ProviderFailure('ServoTelepresenceWake:' + (receipt.reason or receipt.status))
        self.owner.operator = True
        self.telepresent = True

    def telepresence_pose(self, spec, cancel, request_id):
        """The caller's head, lift or visor (servo_poses.telepresence_targets), at a person's look
        pace, inside the calibrated limits. The arms are never moved from their stance."""
        if not self.telepresent:
            return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                reason='TelepresenceStanceNotHeld')
        pose = telepresence_targets(spec, self.owner.commanded)
        if not pose:
            return ServoReceipt(request_id, 'rejected', self.owner.version, self.owner.clock(), self.owner.medium,
                                reason='NothingToMove')
        return self.owner.execute(ServoRequest(request_id, self.owner.version, 'operator', self.owner.clock() + 15.,
                                               pose=pose, look=True), cancel)

    def telepresence_level(self, cancel):
        """The head back to the drive stance (level, lift at its normal height, neck centred) before
        the wheels turn; None when it is already there. The arms hold the stance from the wake."""
        if not self.telepresent:
            return None
        pose = dict(TELEPRESENCE_DRIVE_HEAD)
        if all(abs(self.owner.commanded.get(ch, -99) - value) <= 2 for ch, value in pose.items()):
            return None
        receipt = self.owner.execute(ServoRequest('telepresence:level', self.owner.version, 'operator',
                                                  self.owner.clock() + 15., pose=pose, look=True), cancel)
        if self.trace:
            self.trace.emit('servo_telepresence', kind='level', **asdict(receipt))
        return receipt

    # Sentry mode (owner, October 22) ---------------------------------------------------
    def sentry_stance(self, cancel):
        """On guard every arm stops, the throttle rests and the head is level. All of it is held,
        with no time limit, until sentry_release; talking motion and idle leave it alone."""
        self.owner.end_requested_look()
        self.idle.stop()
        self.reflex.stop()
        self.talking.stop()
        receipt = self.owner.execute(ServoRequest('sentry:stance', self.owner.version, 'pose',
                                                  self.owner.clock() + 60., pose=dict(SENTRY_STANCE)), cancel)
        if receipt.status == 'pulses_reached':
            self.owner.held_channels.update((*ARM_HOLD, *HEAD))
            self.owner.hold_until_released = True
        if self.trace:
            self.trace.emit('servo_sentry', kind='stance', **asdict(receipt))
        return receipt

    def sentry_look(self, neck, cancel, seconds=0., request_id='sentry:look'):
        """Turn the neck (head level) slowly to this pulse: a sweep step or a turn to an intruder."""
        receipt = self.owner.execute(ServoRequest(request_id, self.owner.version, 'sentry',
            self.owner.clock() + 30., pose=sentry_head(neck), travel_seconds=seconds), cancel)
        if self.trace and receipt.status != 'pulses_reached':
            self.trace.emit('servo_sentry', kind='look', neck=int(neck), **asdict(receipt))
        return receipt

    def sentry_arm(self, out, cancel):
        """The throttle arm held out at an intruder, or back at rest."""
        receipt = self.owner.execute(ServoRequest('sentry:arm-out' if out else 'sentry:arm-rest', self.owner.version,
            'pose', self.owner.clock() + 30., pose=dict(SENTRY_ARM_OUT if out else REST)), cancel)
        if self.trace:
            self.trace.emit('servo_sentry', kind='arm_out' if out else 'arm_rest', **asdict(receipt))
        return receipt

    def sentry_release(self, cancel):
        """Off guard: every hold released and ordinary life resumes."""
        receipt = self.execute_pose('release_poses', cancel, 'sentry:release')
        if self.trace:
            self.trace.emit('servo_sentry', kind='release', **asdict(receipt))
        if receipt.status == 'pulses_reached':
            self.idle.wake()
        return receipt

    def note_speech(self):
        """A person is speaking: idle movement and a listening cue stop so the rest is heard cleanly."""
        self._speech_at = self.owner.clock()
        self.idle.stop()
        self.reflex.settle()

    def heard(self):
        """A trusted transcript is back: cock the head while he thinks, unless he is
        already talking or the person has carried on speaking. Calm idle movement steps
        aside so the cue is not refused busy."""
        if self.talking._thread is not None:
            return 'talking'
        if self.owner.clock() - self._speech_at < STILL_TALKING_S:
            return 'person_still_talking'
        self.idle.stop()
        return self.reflex.heard()

    def capture_held(self,capture):
        valid = not capture.motion_risk and self.owner.microphone_held_during(capture.started_at,capture.ended_at)
        self.owner.release_microphone_hold()
        return valid

    def close(self):
        if self.closed:
            return
        self.idle.close()
        self.reflex.close()
        self.talking.stop()  # Failure retains ownership; no close under a worker.
        if self._hand_worker is not None:
            self._hand_worker.join(15.)
        if self._arm_return is not None:
            self._arm_return.join(15.)
            if self._arm_return.is_alive():
                raise ProviderFailure('ArmReturnStillActive')
        self.owner.release_microphone_hold()
        try:
            if self.started and self.owner.state in {'active','stopped','ready'}:
                try:
                    self.before_shutdown()
                except Exception as exc:
                    if self.trace: self.trace.emit('led_fault',reason=type(exc).__name__)
                try:
                    self.on_shutdown()
                except Exception as exc:
                    if self.trace: self.trace.emit('servo_shutdown_hook_failed',error_type=type(exc).__name__)
                self._sequence('shutdown', arms_home=self.telepresence)   # A call's raised poker arm too.
            elif self.started and self.owner.state not in {'parked','closed'}:
                raise ProviderFailure('ServoParkUnavailableAfterFault')
        finally:
            self.owner.close()
            self.closed = True
