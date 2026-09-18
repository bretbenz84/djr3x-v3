"""Read-only XVF3800 control transport. No DSP writes or v2 runtime imports."""
import struct
import time

from ..contracts import ProviderFailure


# Resource, read command, little-endian payload. V2 extraction in V2_REFERENCE.md.
REGISTERS = {
    'routing': (35, 15, 'BB'),
    'bypass': (33, 70, 'B'),
    'azimuth': (33, 75, 'ffff'),
    'energy': (33, 80, 'ffff'),
    'doa': (20, 18, 'HH'),
}


class FlexControl:
    def __init__(self):
        self.device = None

    def start(self):
        import libusb_package
        import usb.backend.libusb1
        import usb.core
        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        if backend is None:
            raise ProviderFailure('FlexUSBBackendMissing')
        devices = list(usb.core.find(find_all=True, idVendor=0x2886,
                                    idProduct=0x001e, backend=backend) or [])
        if len(devices) != 1:
            raise ProviderFailure('FlexUSBMissingOrAmbiguous')
        self.device = devices[0]
        try:
            if self.read('routing') != (8, 0) or self.read('bypass') != (0,):
                raise ProviderFailure('FlexChannelZeroProcessingChanged')
        except BaseException:
            self.close()
            raise

    def read(self, name):
        resource, command, fmt = REGISTERS[name]
        length = struct.calcsize('<' + fmt) + 1
        for attempt in range(3):
            # IN-only vendor control transfer: no configuration change or claim
            # of the audio interface. Bounded timeouts also bound worker shutdown.
            response = bytes(self.device.ctrl_transfer(0xC0, 0, command | 0x80,
                                                      resource, length, timeout=100))
            if not response:
                raise ProviderFailure('FlexEmptyResponse')
            if response[0] == 64:
                time.sleep(.01)
                continue
            if response[0] != 0 or len(response) != length:
                raise ProviderFailure('FlexInvalidResponse')
            return struct.unpack('<' + fmt, response[1:])
        raise ProviderFailure('FlexReadRetryLimit')

    def close(self):
        if self.device is not None:
            import usb.util
            usb.util.dispose_resources(self.device)
            self.device = None
