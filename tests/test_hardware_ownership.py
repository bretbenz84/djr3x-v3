"""Real OS locks in temporary files, fake devices/processes, no hardware I/O."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3x.contracts import Cancellation, Cancelled, ProviderFailure
from r3x.hardware_ownership import HardwareLocks, ControllerHandoff, CompanionResource, serial_port_free
from r3x.operator_cli import commission_rest
from r3x.adapters.maestro import Maestro
from tests.test_servos import PulsePort, Clock


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.locks = HardwareLocks(Path(self.temp.name)/'controller.lock')
        self.clock = Clock()

    def handoff(self, **kwargs):
        return ControllerHandoff(self.locks,port='/dev/cu.synthetic',legacy_active=lambda:False,
                                 clock=self.clock,wait=self.clock.wait,**kwargs)

    def test_legacy_controller_excludes_v3_without_device_probe(self):
        old = self.locks.lease('controller')
        self.assertTrue(old.acquire())
        self.addCleanup(old.close)
        handoff = self.handoff(port_free=lambda _:self.fail('Device probe before ownership'))
        with self.assertRaisesRegex(ProviderFailure,'AnotherController'):
            handoff.acquire(Cancellation())

    def test_resource_release_is_acknowledgment_not_intent(self):
        port = PulsePort()
        companion = CompanionResource(self.locks,'maestro',lambda:port)
        self.assertTrue(companion.poll())
        self.addCleanup(companion.close)
        events = []
        def wait(seconds):
            events.append(self.locks.controller_running())
            companion.poll()
            self.clock.wait(seconds)
        handoff = self.handoff(port_free=lambda _:port.closed)
        handoff.wait = wait
        handoff.acquire(Cancellation())
        self.addCleanup(handoff.close)
        self.assertTrue(port.closed)
        self.assertEqual(events,[True])

    def test_unreleased_resource_times_out_and_drops_intent(self):
        device = self.locks.lease('maestro')
        device.acquire()
        self.addCleanup(device.close)
        handoff = self.handoff(port_free=lambda _:True)
        with self.assertRaisesRegex(ProviderFailure,'Timeout'):
            handoff.acquire(Cancellation(),timeout=.2)
        self.assertFalse(self.locks.controller_running())
        probe = self.locks.lease('microphone')
        self.assertTrue(probe.acquire())
        probe.close()

    def test_busy_or_unknown_legacy_serial_never_opens(self):
        for unknown in (False,True):
            def probe(_):
                if unknown: raise ProviderFailure('UnknownPortOwner')
                return False
            handoff = self.handoff(port_free=probe)
            with self.assertRaises(ProviderFailure): handoff.acquire(Cancellation(),timeout=.15)
            self.assertFalse(self.locks.controller_running())

    def test_cancelled_handoff_releases_every_acquired_lease(self):
        token = Cancellation()
        token.event.set()
        with self.assertRaises(Cancelled): self.handoff(port_free=lambda _:True).acquire(token)
        self.assertFalse(self.locks.controller_running())

    def test_old_supervisor_blocks_even_without_servo_port(self):
        handoff = ControllerHandoff(self.locks,legacy_active=lambda:True,legacy_ready=lambda:False)
        with self.assertRaisesRegex(ProviderFailure,'V2SupervisorHandoffUpgrade'):
            handoff.acquire(Cancellation())
        self.assertFalse(self.locks.controller_running())

    def test_failed_device_close_retains_resource_lease(self):
        class Device:
            def close(self): raise OSError('synthetic close failure')
        companion = CompanionResource(self.locks,'microphone',Device)
        companion.poll()
        with self.assertRaises(OSError): companion.close()
        probe = self.locks.lease('microphone')
        self.assertFalse(probe.acquire())
        companion.lease.close()

    def test_controller_intent_during_companion_acquire_prevents_open(self):
        companion = CompanionResource(self.locks,'microphone',lambda:self.fail('Opened during handoff'))
        with patch.object(self.locks,'controller_running',side_effect=(False,True)):
            self.assertFalse(companion.poll())
        lease = self.locks.lease('microphone')
        self.assertTrue(lease.acquire())
        lease.close()

    def test_os_release_allows_recovery_without_deleting_lock_file(self):
        first = self.locks.lease('maestro')
        first.acquire()
        inode = first.path.stat().st_ino
        first.close()  # Same OS release as a crashed owner's descriptor teardown.
        second = self.locks.lease('maestro')
        self.assertTrue(second.acquire())
        self.assertEqual(second.path.stat().st_ino,inode)
        second.close()

    def test_lsof_checks_both_serial_names_and_fails_on_diagnostics(self):
        from types import SimpleNamespace
        with patch('r3x.hardware_ownership.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='')) as run:
            self.assertTrue(serial_port_free('/dev/cu.synthetic'))
            self.assertIn('/dev/tty.synthetic',run.call_args.args[0])
        with patch('r3x.hardware_ownership.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='permission denied')):
            with self.assertRaises(ProviderFailure): serial_port_free('/dev/cu.synthetic')

    def test_cold_rest_confirmation_never_overwrites_live_or_partial_pulses(self):
        port = PulsePort()
        bus = Maestro(port)
        for confirmed in (False,True):
            with self.assertRaises(ValueError): commission_rest(bus,confirmed=confirmed)
        self.assertEqual(port.targets,[])
        port.pose = {ch:0 for ch in port.pose}
        result = commission_rest(bus,confirmed=True)
        self.assertEqual(result['status'],'rest_pulses_commanded')
        self.assertEqual(port.pose[4],6300)
        self.assertEqual(port.pose[1],2600)

class LegacyCooperationTests(unittest.TestCase):
    def test_registered_supervisor_yields_and_reacquires_after_controller_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller')
            clock=Clock()
            microphone=locks.lease('microphone')
            self.assertTrue(microphone.acquire())
            def wait(seconds):
                self.assertTrue(locks.controller_running())
                microphone.close()
                clock.wait(seconds)
            handoff=ControllerHandoff(locks,legacy_active=lambda:True,legacy_ready=lambda:True,
                clock=clock,wait=wait)
            handoff.acquire(Cancellation())
            self.assertFalse(microphone.acquire())
            handoff.close()
            self.assertTrue(microphone.acquire());microphone.close()

    def test_ready_supervisor_without_microphone_release_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller');clock=Clock()
            microphone=locks.lease('microphone');microphone.acquire()
            try:
                handoff=ControllerHandoff(locks,legacy_active=lambda:True,legacy_ready=lambda:True,
                    clock=clock,wait=clock.wait)
                with self.assertRaisesRegex(ProviderFailure,'Timeout'):
                    handoff.acquire(Cancellation(),timeout=.2)
                self.assertFalse(locks.controller_running())
            finally: microphone.close()

    def test_registration_requires_live_lock_for_every_process_not_stale_files(self):
        from r3x.hardware_ownership import legacy_supervisor_ready,FileLease
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller')
            result=SimpleNamespace(returncode=0,stdout='123 python /v2/rex_supervisor.py\n')
            with patch('r3x.hardware_ownership.subprocess.run',return_value=result):
                self.assertFalse(legacy_supervisor_ready(locks))
                registration=FileLease(locks.controller.with_name('controller.v3-supervisor-v1-123'))
                registration.acquire()
                try:
                    self.assertTrue(legacy_supervisor_ready(locks))
                    result.stdout+='456 python /v2/rex_supervisor.py\n'
                    self.assertFalse(legacy_supervisor_ready(locks))
                finally: registration.close()
                self.assertFalse(legacy_supervisor_ready(locks))

    def test_supervisor_restart_during_handoff_rejects_and_releases_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller')
            results=iter((True,False))
            handoff=ControllerHandoff(locks,legacy_active=lambda:True,legacy_ready=lambda:next(results))
            with self.assertRaisesRegex(ProviderFailure,'HandoffChanged'):
                handoff.acquire(Cancellation())
            self.assertFalse(locks.controller_running())
