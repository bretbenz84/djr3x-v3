"""Off-program servo controls, owned by one polling worker, never UI callbacks."""
import threading

from .adapters.maestro import Maestro
from .contracts import ProviderFailure
from .hardware_ownership import CompanionResource
from .servo_poses import LIMITS, THROTTLE, throttle_clear, validate_pose


class ServoConsole:
    def __init__(self, locks, open_port):
        self.locks = locks
        self.resource = CompanionResource(locks, 'maestro', lambda: Maestro(open_port()))
        self._lock = threading.Lock()
        self.pending = {}
        self.home = False
        self.mode = 'disconnected'
        self.positions = {}
        self.error = None
        self.stopped = threading.Event()
        self.worker = None

    def snapshot(self):
        with self._lock:
            return {'mode':self.mode, 'generation':self.resource.generation,
                    'positions':dict(self.positions), 'error':self.error}

    def target(self, channel, pulse, generation):
        if type(channel) is not int or channel not in LIMITS or type(pulse) is not int:
            raise ValueError('InvalidServoTarget')
        if not LIMITS[channel][0] <= pulse <= LIMITS[channel][1]:
            raise ValueError('ServoTargetOutsideLimits')
        with self._lock:
            if (self.mode != 'live' or generation != self.resource.generation or
                    self.locks.controller_running()):
                return False
            self.pending[channel] = pulse
            return True

    def request_home(self, generation, *, confirmed=False):
        with self._lock:
            if not confirmed:
                raise ValueError('BoardHomeConfirmationRequired')
            if self.mode != 'live' or generation != self.resource.generation or self.locks.controller_running():
                return False
            self.pending.clear()
            self.home = True
            return True

    def poll(self):
        with self._lock:
            try:
                old_generation = self.resource.generation
                ready = self.resource.poll()
                if not ready or old_generation != self.resource.generation:
                    self.pending.clear()
                    self.home = False
                if not ready:
                    self.mode = 'dormant'
                    self.positions.clear()
                    return
                self.mode = 'live'
                bus = self.resource.device
                self.positions = bus.positions(LIMITS)
                pending, self.pending = self.pending, {}
                if self.locks.controller_running():
                    self.resource.close()
                    self.mode = 'dormant'
                    self.home = False
                    self.positions.clear()
                    return
                if self.home:
                    self.home = False
                    bus.go_home()
                    self.positions.clear()  # Refresh next poll, never report commanded as actual.
                elif pending:
                    current = self.positions
                    if set(pending).intersection(THROTTLE):
                        target = {ch:pending.get(ch,current[ch]) for ch in THROTTLE}
                        validate_pose(target)
                        validate_pose({ch:current[ch] for ch in THROTTLE})
                        if not throttle_clear(current,target):
                            raise ValueError('ThrottleManualRouteOutsideClearance')
                        pending.update(target)
                    # Cold joints require explicit commissioning; a slider is not
                    # permission to jump an unknown shaft to its first pulse.
                    if any(current[ch] == 0 for ch in pending):
                        raise ValueError('ColdPoseRequiresCommissioning')
                    validate_pose(pending)
                    bus.profiles({ch:(10,2) if ch in THROTTLE else (30,6) for ch in pending})
                    if not self.locks.controller_running():
                        bus.targets(pending)
                self.error = None
            except ValueError as exc:
                self.error = str(exc)
            except Exception as exc:
                self.error = str(exc) if isinstance(exc,ProviderFailure) else type(exc).__name__
                self.pending.clear()
                self.home = False
                self.mode = 'faulted'
                self.resource.close()
                self.stopped.set()  # No silent reconnect/replay after a wire failure.

    def start(self):
        def run():
            try:
                while not self.stopped.is_set():
                    self.poll()
                    self.stopped.wait(.1)
            finally:
                self.resource.close()
        self.worker = threading.Thread(target=run,name='r3x-servo-console',daemon=True)
        self.worker.start()

    def close(self):
        self.stopped.set()
        if self.worker:
            self.worker.join(3.)
            if self.worker.is_alive():
                raise ProviderFailure('ServoConsoleWorkerStillActive')
        self.resource.close()


def run_menu(console):
    """Explicit macOS UI entry point. Optional dependencies load only here."""
    import rumps
    names = ('Neck','Head lift','Head tilt','Visor','Elbow','Hand','Poker arm','Hero arm',
             'Throttle shoulder','Throttle elbow','Throttle wrist')

    class Menu(rumps.App):
        def __init__(self):
            super().__init__('R3X Servo',title='🦾 Servo Control',quit_button='Quit Servo Control')
            self.status = rumps.MenuItem('Connecting')
            self.sliders = {}
            self.labels = {}
            self.generation = -1
            self.refreshing = False
            items = [self.status,None]
            for ch,(lo,hi) in LIMITS.items():
                label = rumps.MenuItem(names[ch] + ': unread')
                def changed(sender, channel=ch):
                    if not self.refreshing:
                        console.target(channel,round(sender.value),self.generation)
                slider = rumps.SliderMenuItem(value=(lo+hi)/2,min_value=lo,max_value=hi,
                                              callback=changed,dimensions=(220,20))
                self.labels[ch],self.sliders[ch] = label,slider
                items.extend((label,slider))
            items.extend((None,rumps.MenuItem('Restart Pololu (board home)…',callback=self.home)))
            self.menu = items
            self.timer = rumps.Timer(self.refresh,.5)
            self.timer.start()

        def home(self,_):
            answer = rumps.alert('Move all channels to board-stored home positions?',
                'These are Maestro settings, not V3’s validated park route. Confirm the mechanism is clear.',
                ok='Move to board homes',cancel='Cancel')
            if answer == 1:
                console.request_home(self.generation,confirmed=True)

        def refresh(self,_):
            snap = console.snapshot()
            self.status.title = snap['error'] or ('Rex owns servos' if snap['mode']=='dormant' else snap['mode'])
            self.generation = snap['generation']
            self.refreshing = True
            try:
                for ch,slider in self.sliders.items():
                    # rumps 0.4.0 exposes NSSlider here, not MenuItem.hidden.
                    slider._slider.setEnabled_(snap['mode']=='live')
                    pulse = snap['positions'].get(ch)
                    self.labels[ch].title = names[ch] + (f': {pulse / 4:g} µs (output)' if pulse else ': unread/off')
                    if pulse and LIMITS[ch][0] <= pulse <= LIMITS[ch][1]:
                        slider.value = pulse
            finally:
                self.refreshing = False
    console.start()
    try:
        Menu().run()
    finally:
        console.close()
