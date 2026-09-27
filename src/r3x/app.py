"""Explicit composition of the complete core; no hardware starts during inspection."""
from dataclasses import asdict, replace
from pathlib import Path
import platform

from .providers import ProviderOwner, dependency_status
from .settings import ScratchPaths, Settings
from .settings import Secrets


class App:
    def __init__(self, settings: Settings, project: Path, owner: ProviderOwner | None = None):
        self.settings = settings
        self.paths = ScratchPaths.for_project(project)
        self.owner = owner if owner is not None else ProviderOwner()
        self.live_components = None
        self.robot = None
        self.leds = None
        self.drive = None
        self.radar = None
        self.hardware_handoff = None

    def configure_live(self):
        from .adapters.asr import QwenASR
        from .adapters.audio import AudioSink, Microphone, SileroVAD
        from .adapters.hosted import ElevenLabsTTS, OpenAIBrain
        from .adapters.voice import CAMPlus
        from .adapters.camera import Camera
        secrets = Secrets.load(self.paths.root)
        asr = QwenASR(self.paths.root / "models/qwen3-asr", self.settings.asr_trust_logprob)
        vad = SileroVAD(self.settings.vad_threshold)
        brain = OpenAIBrain(secrets.openai_api_key)
        tts = ElevenLabsTTS(secrets.elevenlabs_api_key, secrets.elevenlabs_voice_id)
        sink = AudioSink(self.settings.output_device_name)
        mic = Microphone(self.settings, lambda: False)
        encoder = CAMPlus(self.paths.root / "models/campplus.onnx")
        camera = Camera(self.settings.camera_device_name,self.paths.root / "models/faces",
                        width=self.settings.camera_width, height=self.settings.camera_height,
                        framerate=self.settings.camera_framerate) if self.settings.camera_enabled else None
        from .observations import PublicObservations
        from .scene import SceneObserver
        public = PublicObservations(self.settings) if camera and (self.settings.news_feed_urls or self.settings.weather_enabled) else None
        scene = SceneObserver(camera) if camera and self.settings.scene_observations_enabled else None
        from .objects import Objects
        from .adapters.objects import ObjectDetector
        objects = Objects(camera,ObjectDetector(self.paths.root / 'models/objects/rfdetr-nano.onnx'),scene=scene) if camera and self.settings.object_memory_enabled else None
        from .inspection import CurrentView
        inspection = CurrentView(scene) if scene else None
        from .adapters.flex import FlexControl
        from .direction import Direction
        direction = Direction(FlexControl()) if self.settings.direction_enabled else None
        led_ports = {zone:port for zone,port in (
            ('head',self.settings.head_led_port),('chest',self.settings.chest_led_port)) if port}
        if led_ports:
            from .leds import LEDs
            self.leds = LEDs(led_ports)
        if self.settings.servo_port:
            from .robot_servos import RobotServos
            self.robot = RobotServos(self.settings.servo_port)
        route_planner = None
        if self.settings.drive_port:
            from .drive import DriveBase, Radar
            from .adapters.hosted import OpenAIRoutePlanner
            self.radar = Radar(self.settings.radar_port) if self.settings.radar_port else None
            self.drive = DriveBase(self.settings.drive_port, radar=self.radar)
            route_planner = OpenAIRoutePlanner(secrets.openai_api_key)
        # A disconnected camera removes its dependents for this run, not the setting.
        for provider,requires in ((scene,('camera',)),(inspection,('scene_observations',)),(objects,('camera',))):
            if provider:
                provider.capability = replace(provider.capability,requires=requires)
        self.live_components = (asr, vad, encoder, brain, tts, sink, mic) + tuple(p for p in (camera,public,scene,inspection,direction,objects,route_planner) if p)
        # Audio output is ready for failure notices before device startup.
        # Servo wake/settle completes before microphone or camera starts. Keep
        # tuple indexing stable for core composition; only lifecycle order changes.
        # Wheels connect after servo wake; connecting never moves the base.
        wheels = tuple(p for p in (self.radar, self.drive) if p)
        self.owner = ProviderOwner((tts,sink) + ((self.leds,) if self.leds else ()) + ((self.robot,) if self.robot else ()) + wheels + tuple(p for p in self.live_components if p not in (tts,sink)))

    def readiness(self):
        missing = []
        if self.live_components:
            for name, ready in Secrets.load(self.paths.root).readiness().items():
                if not ready:
                    missing.append(name)
            if not self.settings.input_device_name and self.settings.input_device_index is None:
                missing.append("microphone_selection")
            model = self.paths.root / "models/qwen3-asr"
            if model.resolve() != model or not all((model / name).is_file() for name in
                    ("config.json", "model.safetensors", "vocab.json", "merges.txt", "preprocessor_config.json")):
                missing.append("asr_model_files")
            voice = self.paths.root / "models/campplus.onnx"
            if voice.resolve() != voice or not voice.is_file():
                missing.append("voice_model_file")
            if self.settings.camera_enabled:
                import shutil
                if not shutil.which("ffmpeg"):
                    missing.append("ffmpeg")
                for name in ("scrfd_10g.onnx", "edgeface_s_gamma_05.onnx"):
                    if not (self.paths.root / "models/faces" / name).is_file():
                        missing.append(name)
            if self.settings.camera_enabled and self.settings.object_memory_enabled and not (self.paths.root / 'models/objects/rfdetr-nano.onnx').is_file():
                missing.append('object_model_file')
            for provider in self.owner.providers:
                missing.extend(name for name, version in dependency_status(provider.capability).items()
                               if version is None)
        return sorted(set(missing))

    def doctor(self) -> dict:
        return {
            "profile": self.settings.profile,
            "python": platform.python_version(),
            "slice": "0-4-core" if self.live_components else "0-independent-boot",
            "conversation_ready": bool(self.live_components and self.owner.state == "ready"),
            "missing": self.readiness(),
            "scratch_root": str(self.paths.root),
            "lifecycle": self.owner.state,
            "capabilities": [
                {**asdict(p.capability), "dependencies": dependency_status(p.capability)}
                for p in self.owner.providers
            ],
            "results": [asdict(result) for result in self.owner.results],
            "camera_enabled": self.settings.camera_enabled,
            "record_evaluation": self.settings.record_evaluation,
            "limitations": ["Held-out speaker accuracy and live group acceptance remain unmeasured",
                            "Window consistency is not a validated overlap detector",
                            "Face thresholds are provisional; robot-camera and M2 validation remain pending",
                            "Dependency presence is not provider/device validation"],
        }

    def start(self) -> bool:
        self.paths.prepare()
        return self.owner.start()

    def close(self) -> None:
        self.owner.close()

    def live(self):
        from uuid import uuid4
        from .output import OutputOwner
        from .runtime import VoiceRuntime
        from .session import Session
        from .trace import Trace
        from .terminal import LiveConsole
        from .lease import LiveLease
        from .memory import Memory
        from .state import CoreState
        from .speakers import SpeakerTracker
        from .adapters.voice import NAMESPACE
        from .perception import Perception
        from .dataset import CaptureRecorder
        self.configure_live()
        if self.readiness():
            return False
        self.paths.prepare()
        lease = LiveLease(self.paths.root / "sessions/live.lock")
        lease.acquire()
        self.live_lease = lease  # Retain if cleanup fails while the process remains alive.
        from .hardware_ownership import ControllerHandoff, HardwareLocks
        from .contracts import Cancellation
        self.hardware_handoff = ControllerHandoff(HardwareLocks(),port=self.settings.servo_port or None,
            led_ports={zone:port for zone,port in (("head_led",self.settings.head_led_port),
                ("chest_led",self.settings.chest_led_port),("drive_base",self.settings.drive_port),
                ("radar",self.settings.radar_port)) if port})
        try:
            self.hardware_handoff.acquire(Cancellation())
        except BaseException:
            lease.close()
            raise
        session_id = uuid4().hex
        try:
            log_path = self.paths.root / "sessions" / (session_id + ".jsonl")
            trace = Trace(session_id, log_path, display=LiveConsole())
            trace.emit("starting", log_path=str(log_path))
        except BaseException:
            self.hardware_handoff.close()
            lease.close()
            raise
        asr, vad, encoder, brain, tts, sink, mic, *optional = self.live_components
        brain.trace = trace
        camera = next((p for p in optional if p.capability.name=='camera'),None)
        public = next((p for p in optional if p.capability.name=='public_observations'),None)
        scene = next((p for p in optional if p.capability.name=='scene_observations'),None)
        objects = next((p for p in optional if p.capability.name=='objects'),None)
        for provider in (public,scene,objects):
            if provider:provider.trace=trace
        if camera:
            camera.trace = trace
        memory = Memory(self.paths.root / "core.sqlite3")
        inspection = next((p for p in optional if p.capability.name=='current_view'),None)
        output = OutputOwner(tts, sink, trace,inspection=inspection)
        if self.leds:
            self.leds.trace = trace
            sink.on_pcm = self.leds.pcm
            if self.robot:
                self.robot.before_shutdown = self.leds.cancel
        mic.echo_risk = output.echo_risk
        mic.trace = trace
        if self.robot:
            self.robot.trace = trace
        if self.drive:
            self.drive.trace = trace
        def motion_risk():
            return ((self.robot is not None and self.robot.owner.motion_risk()) or
                    (self.drive is not None and self.drive.motion_risk()))
        if self.robot or self.drive:
            mic.motion_risk = motion_risk
        direction = next((p for p in optional if p.capability.name=='direction'),None)
        if direction:
            direction.trace = trace
            direction.echo_risk = lambda: output.echo_risk() or motion_risk()
            if self.robot:
                self.robot.owner.on_motion = direction.invalidate
            if self.drive:
                self.drive.on_motion.append(lambda plan: direction.invalidate())
        runtime = None
        success = False
        import signal
        def terminate(*_):
            raise KeyboardInterrupt()
        previous_sigterm = signal.signal(signal.SIGTERM,terminate)
        try:
            memory.start()
            if camera:
                from .adapters.face import NAMESPACE as FACE_NAMESPACE
                camera.session_id = session_id
                camera.profiles = lambda: memory.face_profiles(FACE_NAMESPACE)
            from .startup_notice import announce
            if not self.owner.start(on_required_failure=lambda:announce(
                    self.settings,self.owner,output,trace,failed=True)):
                trace.emit("startup_failed", report=self.doctor())
            else:
                trace.emit("startup", report=self.doctor(), evidence="live", models=[p.capability.model for p in self.live_components if p.capability.model])
                active = {r.capability for r in self.owner.results if r.operation=='start' and r.outcome=='ok'}
                camera = camera if 'camera' in active else None
                scene = scene if 'scene_observations' in active else None
                objects = objects if 'objects' in active else None
                output.inspection = inspection if 'current_view' in active else None
                startup_delivery = announce(self.settings,self.owner,output,trace)
                mic.discard_pending()
                output.robot = self.robot
                output.speech_motion = self.robot.talking if self.robot else None
                output.leds = self.leds
                if self.drive and 'drive_base' not in active:
                    # Configured wheels that failed to start: Rex still knows he has them.
                    failure = next((r for r in self.owner.results if r.capability=='drive_base' and r.operation=='start'),None)
                    output.drive_unavailable = 'wheel_controller_not_connected' + (':'+failure.reason if failure and failure.reason else '')
                if self.drive and 'drive_base' in active:
                    output.drive = self.drive
                    self.drive.servos = self.robot if self.robot and 'robot_servos' in active else None
                    self.drive.faces = camera.latest if camera else None
                    self.drive.objects = objects.snapshot if objects else None
                state = CoreState(memory,SpeakerTracker(NAMESPACE),trace, face_invalidator=camera.invalidate if camera else None,
                                  require_microphone_hold=self.robot is not None)
                state.observations,state.scene_observer=public,scene
                if objects:
                    from .photo_memory import PhotoMemory
                    state.objects=objects
                    state.photo_memory=PhotoMemory(self.paths.root / 'visual-memory')
                    state.photo_memory.start()
                perception = Perception(asr,encoder,trace,camera,direction)
                session = Session(session_id, brain, output, trace, state=state)
                planner = next((p for p in optional if p.capability.name=='route_planner'),None)
                if planner and output.drive and 'route_planner' in active:
                    planner.trace = trace
                    session.route_planner = planner
                if startup_delivery and startup_delivery.completed_text:
                    session.history.append({'role':'assistant','content':' '.join(startup_delivery.completed_text)})
                if self.settings.camera_enabled and camera is None:
                    session.history.append({'role':'developer','content':
                        'Camera unavailable at startup. No current visual evidence is available this run; do not claim to see.'})
                recorder = CaptureRecorder(self.paths.root / "recordings",session_id) if self.settings.record_evaluation else None
                runtime = VoiceRuntime(mic, vad, asr, session, trace, self.settings.end_silence_seconds,perception,recorder,
                                       lull_seconds=self.settings.proactive_lull_seconds,observations=public,scene_observer=scene,robot=self.robot,
                                       drive=output.drive)
                if output.drive:
                    output.drive.on_motion.append(lambda plan: runtime.arrivals.note_base_motion(
                        trace.clock(), allow_new=bool(plan and plan.allow_new_arrivals)))
                runtime.run()
                success = True
        finally:
            if runtime and (any(w.is_alive() for w in runtime.workers) or
                            (runtime.session.output_worker is not None and runtime.session.output_worker.is_alive())):
                # Do not unload native models under an unfinished worker.
                trace.emit("shutdown_failed", reason="active_worker_resources_retained_until_process_exit")
            else:
                self.close()
                if runtime and runtime.session.state.photo_memory:runtime.session.state.photo_memory.close()
                memory.close()
                trace.emit("shutdown", report=self.doctor())
                # Only acknowledge handoff after device closure, including failed
                # startup cleanup. A stuck/failed closer retains the leases.
                if self.owner.state == 'closed':
                    self.hardware_handoff.close()
            trace.close()
            if self.owner.state == 'closed':
                lease.close()
            signal.signal(signal.SIGTERM,previous_sigterm)
        return success and self.owner.state == "closed"
