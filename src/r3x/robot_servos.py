"""Explicit robot lifecycle, with park before port release."""
from dataclasses import asdict
import time
from .adapters.maestro import open_serial
from .contracts import Cancellation, ProviderFailure
from .providers import Capability
from .servo_poses import HEAD, LIMITS, pose_channels
from .servos import ServoOwner, ServoRequest, TalkingMotion
from .idle_motion import IdleMotion
from .voice_reflex import VoiceReflex


# A picture asked for after a drive has a 3 s capture window from 0.3 s after the stop.
LOOK_STILL_S = 4.


class RobotServos:
    capability = Capability('robot_servos','Pololu Maestro','startup, idle arms, talking, microphone hold and park',
        dependencies=('pyserial',),resources=('exclusive Maestro; fourteen calibrated channels',),
        shutdown='join idle/talking; park; close port; acknowledge ownership release')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=time.sleep):
        self.owner = ServoOwner(open_port or (lambda:open_serial(port)),medium='controller',clock=clock)
        self.idle = IdleMotion(self.owner)
        self.talking = TalkingMotion(self.owner)
        self.reflex = VoiceReflex(self.owner)
        self.reflex.on_voice = self.idle.look_at_people   # Someone is talking: look at them again.
        def quiet():
            self.idle.stop()
            self.reflex.stop()
        self.talking.before_start = quiet
        self.idle.busy = lambda: self.talking._thread is not None
        self.trace = None
        self.wait = wait
        self.closed = False
        self.started = False
        self.before_shutdown = lambda: None
        self.on_startup = lambda: None   # Startup sounds begin with the wake choreography.
        self._arm_return = None

    def _sequence(self, kind):
        result = self.owner.execute(ServoRequest(kind,self.owner.version,kind,self.owner.clock()+120.),Cancellation())
        if self.trace:
            self.trace.emit('servo_lifecycle',kind=kind,**asdict(result))
        if result.status != 'pulses_reached':
            raise ProviderFailure('Servo'+kind.title()+':'+(result.reason or result.status))

    def start(self):
        self.owner.start()
        self.started = True
        if self.trace and self.owner.energized_channels:
            self.trace.emit('servo_unpowered_rest', channels=list(self.owner.energized_channels))
        try:
            self.on_startup()
        except Exception as exc:
            if self.trace: self.trace.emit('servo_startup_hook_failed',error_type=type(exc).__name__)
        self._sequence('startup')
        # Capture is started later by ProviderOwner, after motion/hold settling.
        self.wait(.5)
        self.owner.refresh_microphone_hold()
        self.wait(1.5)

    def cancel(self):
        self.idle.close()
        self.reflex.close()
        self.talking.cancel()
        self.owner.cancel()

    def before_frame(self, *, allow_idle=False, capturing=False, calm=False):
        self.idle.trace = self.reflex.trace = self.trace
        self.idle.tick(allow_idle and self.talking._thread is None, calm)
        if (not capturing and self.talking._thread is None and self.idle._thread is None and
                not (self.reflex._thread and self.reflex._thread.is_alive())):
            self.owner.refresh_microphone_hold()

    def execute_pose(self, name, cancel, request_id, *, hold_pose=True):
        self.idle.stop()
        self.reflex.stop()   # The model's explicit move replaces an unfinished reflex.
        self.talking.stop()
        if name == 'hold_arms':
            # Nothing moves and nothing is heard, so there is nothing for newer speech to
            # interrupt: October 3, "keep your hand down and don't move it" was understood
            # and then cancelled 0.2 s later by someone else talking.
            cancel = Cancellation()
        follow = name == 'look_at_people'
        if follow:
            # Following faces is the idle gaze's job, never a held pose. When a person asks
            # for it, their earlier held look gives way; Rex's own cannot override a hold.
            if hold_pose:
                self.owner.held_channels.difference_update(HEAD)
            hold_pose = False
            try:
                aim = self.idle.face() if self.idle.face else None
            except Exception:
                aim = None
            if aim:
                name = {0: max(LIMITS[0][0], min(LIMITS[0][1], int(aim['neck'])))}
        receipt = self.owner.execute(ServoRequest(request_id,self.owner.version,'pose',
            self.owner.clock()+60,pose=name,hold_pose=hold_pose),cancel)
        if receipt.status == 'pulses_reached':
            if follow or name == 'release_poses':
                self.idle.look_at_people()
            else:
                self.idle.posed(receipt.changed_channels or pose_channels(name), own=not hold_pose)
        return receipt

    def drive_look(self, pose, cancel, request_id):
        """A head move the drive owner makes to look at something mid-drive. The drive
        stance already holds the head, and this is nobody's requested pose: it must not
        start the hold that keeps Rex's own movement off a person's pose for 90 seconds."""
        held_at = self.owner.pose_held_at
        try:
            return self.execute_pose(pose, cancel, request_id)
        finally:
            self.owner.pose_held_at = held_at

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
        """Park the throttle arm and center the neck before any non-reverse base motion."""
        self.idle.stop()
        self.reflex.stop()
        self.talking.stop()
        if self._arm_return is not None:
            self._arm_return.join(15.)
            if self._arm_return.is_alive():
                from .servos import ServoReceipt
                return ServoReceipt('base-park', 'rejected', self.owner.version, self.owner.clock(),
                                    self.owner.medium, reason='ArmReturnStillActive')
            self._arm_return = None
        receipt = self.owner.execute(ServoRequest('base-park', self.owner.version, 'base_park',
                                                  self.owner.clock()+30.), cancel)
        if self.trace:
            self.trace.emit('servo_base_park', **asdict(receipt))
        return receipt

    def finish_base_motion(self, look_pending=False):
        """The drive is over: the head and the other servos are free at once, while the
        throttle arm returns to rest in the background.

        Owner, October 3: he stayed frozen until the arm had un-parked. Talking and idle
        motion now carry on beside the return (they never command the arm's channels while
        it is on its way). A later drive plan or shutdown joins this worker first.

        look_pending: a picture is about to be taken from where he stopped, so the head
        stays still for it first (the session wakes idle motion once it is taken).
        """
        import threading
        self.owner.begin_arm_return()
        self.idle.look_at_people()   # The drive centred the head; an earlier held look is over.
        self.idle.wake(delay=LOOK_STILL_S if look_pending else 0.)   # Now, not after the usual idle pause.
        def run():
            receipt = self.owner.return_arm()
            if self.trace:
                self.trace.emit('servo_base_release', **asdict(receipt))
        self._arm_return = threading.Thread(target=run, name='r3x-arm-return', daemon=True)
        self._arm_return.start()

    def note_speech(self):
        self.idle.stop()

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
                self._sequence('shutdown')
            elif self.started and self.owner.state not in {'parked','closed'}:
                raise ProviderFailure('ServoParkUnavailableAfterFault')
        finally:
            self.owner.close()
            self.closed = True
