"""Explicit robot lifecycle, with park before port release."""
from dataclasses import asdict
import time
from .adapters.maestro import open_serial
from .contracts import Cancellation, ProviderFailure
from .providers import Capability
from .servos import ServoOwner, ServoRequest, TalkingMotion
from .idle_motion import IdleMotion
from .voice_reflex import VoiceReflex


class RobotServos:
    capability = Capability('robot_servos','Pololu Maestro','startup, idle arms, talking, microphone hold and park',
        dependencies=('pyserial',),resources=('exclusive Maestro; eleven calibrated channels',),
        shutdown='join idle/talking; park; close port; acknowledge ownership release')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=time.sleep):
        self.owner = ServoOwner(open_port or (lambda:open_serial(port)),medium='controller',clock=clock)
        self.idle = IdleMotion(self.owner)
        self.talking = TalkingMotion(self.owner)
        self.reflex = VoiceReflex(self.owner)
        def quiet():
            self.idle.stop()
            self.reflex.stop()
        self.talking.before_start = quiet
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

    def before_frame(self, *, allow_idle=False, capturing=False):
        self.idle.trace = self.reflex.trace = self.trace
        self.idle.tick(allow_idle and self.talking._thread is None)
        if (not capturing and self.talking._thread is None and self.idle._thread is None and
                not (self.reflex._thread and self.reflex._thread.is_alive())):
            self.owner.refresh_microphone_hold()

    def execute_pose(self, name, cancel, request_id):
        self.idle.stop()
        self.reflex.stop()   # The model's explicit move replaces an unfinished reflex.
        self.talking.stop()
        return self.owner.execute(ServoRequest(request_id,self.owner.version,'pose',
            self.owner.clock()+60,pose=name),cancel)

    def execute_expression(self, request, cancel):
        self.idle.stop()
        self.reflex.stop()
        self.talking.stop()
        cancel.check()
        from dataclasses import replace
        receipt = self.owner.execute(replace(request,version=self.owner.version),cancel)
        if self.trace:self.trace.emit('servo_expression',**asdict(receipt))
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

    def finish_base_motion(self):
        """Return the arm to rest in the background; speech need not wait for it.

        Talking motion is rejected (ServoBusy) while the return runs; audio is not.
        A later drive plan or shutdown joins this worker before using the owner.
        """
        import threading
        def run():
            receipt = self.owner.execute(ServoRequest('base-release', self.owner.version, 'base_release',
                                                      self.owner.clock()+30.), Cancellation())
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
