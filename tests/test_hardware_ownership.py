"""Real OS locks in temporary files, fake devices/processes, no hardware I/O."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3x.contracts import Cancellation, Cancelled, ProviderFailure
from r3x.hardware_ownership import HardwareLocks, ControllerHandoff, CompanionResource, serial_port_free
from r3x.servo_console import ServoConsole
from r3x.supervisor import Supervisor
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
        handoff = ControllerHandoff(self.locks,legacy_active=lambda:True)
        with self.assertRaisesRegex(ProviderFailure,'V2Supervisor'):
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

    def test_console_discards_slider_commands_across_handoff(self):
        ports = []
        def open_port():
            port = PulsePort()
            ports.append(port)
            return port
        console = ServoConsole(self.locks,open_port)
        self.addCleanup(console.close)
        console.poll()
        generation = console.snapshot()['generation']
        self.assertTrue(console.target(0,6000,generation))
        main = self.locks.lease('controller')
        main.acquire()
        console.poll()
        self.assertTrue(ports[0].closed)
        self.assertEqual(ports[0].targets,[])
        self.assertFalse(console.target(0,6100,generation))
        main.close()
        console.poll()
        self.assertEqual(ports[1].targets,[])
        self.assertFalse(console.target(0,6200,generation))
        self.assertTrue(console.target(0,6200,console.snapshot()['generation']))
        console.poll()
        self.assertEqual(ports[1].pose[0],6200)

    def test_console_throttle_rejects_unsafe_individual_slider(self):
        port = PulsePort()
        console = ServoConsole(self.locks,lambda:port)
        self.addCleanup(console.close)
        console.poll()
        console.target(9,2200,console.snapshot()['generation'])
        console.poll()
        self.assertEqual(console.snapshot()['error'],'ThrottleManualRouteOutsideClearance')
        self.assertEqual(port.targets,[])

    def test_home_requires_confirmation_and_clears_sliders(self):
        port = PulsePort()
        console = ServoConsole(self.locks,lambda:port)
        self.addCleanup(console.close)
        console.poll()
        gen = console.snapshot()['generation']
        console.target(0,6000,gen)
        with self.assertRaises(ValueError): console.request_home(gen)
        console.request_home(gen,confirmed=True)
        console.poll()
        self.assertEqual(port.targets,[])
        self.assertIn(bytes((0xA2,)),port.packets)

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

    def test_supervisor_closes_mic_before_launch_and_resumes_after_exit(self):
        events = []
        class Listener:
            def heard(self): return True
            def close(self): events.append('closed')
        class Child:
            code = None
            def poll(self): return self.code
        child = Child()
        def launch():
            events.append('launched')
            return child
        supervisor = Supervisor(self.locks,Listener,launch)
        self.addCleanup(supervisor.close)
        self.assertEqual(supervisor.tick(),'launched')
        self.assertEqual(events,['closed','launched'])
        self.assertEqual(supervisor.tick(),'controller')
        child.code = 1
        self.assertEqual(supervisor.tick(),'launched') # New synthetic wake, not crash autorestart.
        self.assertEqual(supervisor.last_exit,1)

    def test_manual_controller_suppresses_supervisor_listening(self):
        lease = self.locks.lease('controller')
        lease.acquire()
        self.addCleanup(lease.close)
        supervisor = Supervisor(self.locks,lambda:self.fail('Mic opened'),lambda:self.fail('Launched'))
        self.assertEqual(supervisor.tick(),'dormant')

    def test_failed_launch_leaves_mic_closed_and_next_tick_can_listen(self):
        class Listener:
            closed = False
            def heard(self): return True
            def close(self): self.closed = True
        listener = Listener()
        supervisor = Supervisor(self.locks,lambda:listener,lambda:None)
        with self.assertRaises(ProviderFailure): supervisor.tick()
        self.assertTrue(listener.closed)
        probe = self.locks.lease('microphone')
        self.assertTrue(probe.acquire())
        probe.close()

    def test_prepared_bundle_is_independent_and_does_not_install_agents(self):
        import plistlib
        import json
        from tools.prepare_operator_migration import prepare
        from r3x.settings import Settings
        root=Path(self.temp.name)
        project,source,agents=root/'project',root/'source',root/'agents'
        project.mkdir();source.mkdir();agents.mkdir()
        (source/'_openwakeword_resources').mkdir()
        for name in ('wakeuprex.onnx','_openwakeword_resources/melspectrogram.onnx','_openwakeword_resources/embedding_model.onnx'):
            (source/name).write_bytes(b'synthetic model bytes')
        for role in ('supervisor','servo','led','battery'):
            (agents/f'com.djr3x.{role}.plist').write_bytes(plistlib.dumps({'Label':f'com.djr3x.{role}'}))
        originals={p.name:p.read_bytes() for p in agents.iterdir()}
        bundle=prepare(project,source,'/dev/cu.synthetic',agents)
        self.assertEqual({p.name:p.read_bytes() for p in agents.iterdir()},originals)
        self.assertEqual(Settings.load(bundle/'robot-settings.toml').servo_port,'/dev/cu.synthetic')
        job=plistlib.loads((bundle/'com.djr3x.v3.supervisor.plist').read_bytes())
        self.assertEqual(job['ProgramArguments'][0],str(project/'.venv/bin/python'))
        self.assertEqual(len(json.loads((bundle/'inventory.json').read_text())['original_agents']),4)
        target=project/'local/models/wake/wakeuprex.onnx'
        self.assertNotEqual(target.stat().st_ino,(source/'wakeuprex.onnx').stat().st_ino)

    def test_wake_requires_four_consecutive_scores_and_resets_on_overflow(self):
        import sys
        from types import SimpleNamespace
        from r3x.adapters.wake import WakeListener
        from r3x.settings import Settings
        listener=WakeListener(Settings(),Path(self.temp.name))
        values=iter((.99,.99,.1,.99,.99,.99,.99))
        listener.model=SimpleNamespace(predict=lambda _: {'wakeuprex':next(values)},reset=lambda:None)
        stream=SimpleNamespace(read_available=1280,read=lambda _: (bytes(2560),False))
        listener.stream=stream
        with patch.dict(sys.modules,{'numpy':SimpleNamespace(frombuffer=lambda data,**_:data)}):
            self.assertEqual([listener.heard() for _ in range(7)],[False]*6+[True])
            stream.read=lambda _: (bytes(2560),True)
            self.assertFalse(listener.heard())
            self.assertEqual(listener.consecutive,0)
