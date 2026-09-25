"""Explicit robot lifecycle, with park before port release and no cold assumptions."""
from dataclasses import asdict
import time
from .adapters.maestro import open_serial
from .contracts import Cancellation, ProviderFailure
from .providers import Capability
from .servos import ServoOwner, ServoRequest, TalkingMotion


class RobotServos:
    capability = Capability('robot_servos','Pololu Maestro','startup, talking, microphone hold and park',
        dependencies=('pyserial',),resources=('exclusive Maestro; eleven calibrated channels',),
        shutdown='join talking; park; close port; acknowledge ownership release')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=time.sleep):
        self.owner = ServoOwner(open_port or (lambda:open_serial(port)),medium='controller',clock=clock)
        self.talking = TalkingMotion(self.owner)
        self.trace = None
        self.wait = wait
        self.closed = False
        self.started = False

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
        self.talking.cancel()
        self.owner.cancel()

    def before_frame(self):
        self.owner.refresh_microphone_hold()

    def capture_held(self,capture):
        valid = self.owner.microphone_held_during(capture.started_at,capture.ended_at)
        self.owner.release_microphone_hold()
        return valid

    def close(self):
        if self.closed:
            return
        self.talking.stop()  # Failure retains ownership; no close under a worker.
        self.owner.release_microphone_hold()
        try:
            if self.started and self.owner.state in {'active','stopped','ready'}:
                self._sequence('shutdown')
            elif self.started and self.owner.state not in {'parked','closed'}:
                raise ProviderFailure('ServoParkUnavailableAfterFault')
        finally:
            self.owner.close()
            self.closed = True
