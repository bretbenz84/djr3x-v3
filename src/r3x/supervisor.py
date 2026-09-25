"""Wake/start supervision with acknowledged microphone handoff; no auto-updater."""
from .hardware_ownership import CompanionResource
from .contracts import ProviderFailure


class Supervisor:
    def __init__(self, locks, open_listener, launch):
        self.locks, self.launch = locks, launch
        self.listener = CompanionResource(locks,'microphone',open_listener)
        self.child = None
        self.last_exit = None

    def tick(self):
        if self.child is not None:
            code = self.child.poll()
            if code is None:
                self.listener.close()
                return 'controller'
            self.last_exit = code
            self.child = None
        if not self.listener.poll():
            return 'dormant'
        if self.listener.device.heard():
            self.listener.close()  # A close failure must prevent launch.
            if self.locks.controller_running():
                return 'dormant'
            self.child = self.launch()
            if self.child is None:
                raise ProviderFailure('ControllerLaunchFailed')
            return 'launched'
        return 'listening'

    def close(self):
        # The child is independent; quitting the listener does not kill Rex.
        self.listener.close()
