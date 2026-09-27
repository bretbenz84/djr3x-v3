"""Explicit robot lifecycle, with park before port release and no cold assumptions."""
from dataclasses import asdict
import time
from .adapters.maestro import open_serial
from .contracts import Cancellation, ProviderFailure
from .providers import Capability
from .servos import ServoOwner, ServoRequest, TalkingMotion
from .idle_motion import IdleMotion


class RobotServos:
    capability = Capability('robot_servos','Pololu Maestro','startup, idle arms, talking, microphone hold and park',
        dependencies=('pyserial',),resources=('exclusive Maestro; eleven calibrated channels',),
        shutdown='join idle/talking; park; close port; acknowledge ownership release')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=time.sleep):
        self.owner = ServoOwner(open_port or (lambda:open_serial(port)),medium='controller',clock=clock)
        self.idle = IdleMotion(self.owner)
        self.talking = TalkingMotion(self.owner)
        self.talking.before_start = self.idle.stop
        self.trace = None
        self.wait = wait
        self.closed = False
        self.started = False
        self.before_shutdown = lambda: None

    def _sequence(self, kind):
        result = self.owner.execute(ServoRequest(kind,self.owner.version,kind,self.owner.clock()+120.),Cancellation())
        if self.trace:
            self.trace.emit('servo_lifecycle',kind=kind,**asdict(result))
        if result.status != 'pulses_reached':
            raise ProviderFailure('Servo'+kind.title()+':'+(result.reason or result.status))

    def start(self):
        self.owner.start()
        self.started = True
        self._sequence('startup')
        # Capture is started later by ProviderOwner, after motion/hold settling.
        self.wait(.5)
        self.owner.refresh_microphone_hold()
        self.wait(1.5)

    def cancel(self):
        self.idle.close()
        self.talking.cancel()
        self.owner.cancel()

    def before_frame(self, *, allow_idle=False, capturing=False):
        self.idle.trace = self.trace
        self.idle.tick(allow_idle and self.talking._thread is None)
        if not capturing and self.talking._thread is None and self.idle._thread is None:
            self.owner.refresh_microphone_hold()

    def execute_pose(self, name, cancel, request_id):
        self.idle.stop()
        self.talking.stop()
        return self.owner.execute(ServoRequest(request_id,self.owner.version,'pose',
            self.owner.clock()+60,pose=name),cancel)

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
        self.talking.stop()  # Failure retains ownership; no close under a worker.
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
