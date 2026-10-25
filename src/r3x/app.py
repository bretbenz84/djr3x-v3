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
        self.startup_audio = None
        self.hardware_handoff = None
        self.cloner = None

    def configure_live(self):
        from .adapters.asr import QwenASR
        from .adapters.audio import AudioSink, Microphone, SileroVAD
        from .adapters.hosted import ElevenLabsTTS, OpenAIBrain, OpenAIAgencyPlanner, OpenAIJokeWriter
        from .adapters.voice import CAMPlus
        from .adapters.camera import Camera
        secrets = Secrets.load(self.paths.root)
        asr = QwenASR(self.paths.root / "models/qwen3-asr", self.settings.asr_trust_logprob)
        vad = SileroVAD(self.settings.vad_threshold)
        brain = OpenAIBrain(secrets.openai_api_key, self.settings.reply_service_tier)
        agency_planner = OpenAIAgencyPlanner(secrets.openai_api_key,self.settings.agency_planner_model)
        joke_writer = (OpenAIJokeWriter(secrets.openai_api_key,self.settings.agency_planner_model)
                       if self.settings.joke_writer_enabled else None)
        from .voice_modes import VOICES
        tts = ElevenLabsTTS(secrets.elevenlabs_api_key, secrets.elevenlabs_voice_id, VOICES)
        from .speech_level import PCMLeveler
        tts.level = PCMLeveler   # V2 per-phrase loudness leveling (numpy/scipy load on first speech).
        sink = AudioSink(self.settings.output_device_name)
        mic = Microphone(self.settings, lambda: False)
        encoder = CAMPlus(self.paths.root / "models/campplus.onnx")
        camera = Camera(self.settings.camera_device_name,self.paths.root / "models/faces",
                        width=self.settings.camera_width, height=self.settings.camera_height,
                        framerate=self.settings.camera_framerate) if self.settings.camera_enabled else None
        from .observations import PublicObservations
        from .scene import SceneObserver
        from .adapters.hosted import OpenAINewsSearch
        from .news import NewsCache
        search = OpenAINewsSearch(secrets.openai_api_key) if self.settings.news_search_enabled else None
        public = PublicObservations(self.settings, search=search, cache=NewsCache(self.paths.root / 'cache/news.json')) \
            if self.settings.news_feed_urls or self.settings.weather_enabled or search else None
        scene = SceneObserver(camera) if camera and self.settings.scene_observations_enabled else None
        from .weather import Weather
        weather = Weather(self.settings) if self.settings.weather_enabled else None
        from .objects import Objects
        from .adapters.objects import ObjectDetector
        objects = Objects(camera,ObjectDetector(self.paths.root / 'models/objects/rfdetr-nano.onnx'),scene=scene) if camera and self.settings.object_memory_enabled else None
        places = None
        if camera and self.settings.place_recognition_enabled:
            from .adapters.places import PlaceEncoder
            from .places import Places
            places = Places(camera, PlaceEncoder(self.paths.root / 'models/places/mobileclip-s2.onnx'), None)
        from .inspection import CurrentView
        inspection = CurrentView(scene, camera=camera) if scene else None
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
        locator = None
        if self.settings.drive_port:
            from .drive import DriveBase, Radar
            self.radar = Radar(self.settings.radar_port) if self.settings.radar_port else None
            self.drive = DriveBase(self.settings.drive_port, radar=self.radar)
            if camera:
                from .adapters.hosted import OpenAIVisualLocator
                locator = OpenAIVisualLocator(secrets.openai_api_key)
        from .adapters.keywords import WORDS
        keywords = None
        if any((self.paths.root / 'models/wake' / (w + '.onnx')).is_file() for w in WORDS):
            # The owner's keywords (October 25): "stop" while the wheels run, "Hey Rex" over his voice.
            from .adapters.keywords import KeywordSpotter
            keywords = KeywordSpotter(self.paths.root / 'models/wake')
        if self.settings.impersonation_enabled:
            from .adapters.breeze import VoiceCloner
            self.cloner = VoiceCloner(self.paths.root / 'models/breeze-tts-8bit', level=PCMLeveler)
        # A disconnected camera removes its dependents for this run, not the setting.
        for provider,requires in ((scene,('camera',)),(inspection,('scene_observations',)),(objects,('camera',)),(places,('camera',))):
            if provider:
                provider.capability = replace(provider.capability,requires=requires)
        self.live_components = (asr, vad, encoder, brain, tts, sink, mic) + tuple(p for p in (camera,public,weather,scene,inspection,direction,objects,places,locator,agency_planner,joke_writer,self.cloner,keywords) if p)
        # Audio output is ready for failure notices before device startup.
        # Servo wake/settle completes before microphone or camera starts. Keep
        # tuple indexing stable for core composition; only lifecycle order changes.
        # Wheels connect after servo wake; connecting never moves the base.
        wheels = tuple(p for p in (self.radar, self.drive) if p)
        # V2 startup sounds start as soon as speech output is ready and play over
        # servo wake and model loading; live() stops them before the ready line.
        from .startup_audio import StartupAudio
        self.startup_audio = StartupAudio(tts, sink, self.paths.root / 'cache', robot=self.robot, leds=self.leds)
        if self.robot:
            self.robot.on_startup = self.startup_audio.motion_started
            self.robot.on_shutdown = self.startup_audio.shutdown_started   # Power-down clip over the park.
        self.owner = ProviderOwner((tts,sink,self.startup_audio) + ((self.leds,) if self.leds else ()) + ((self.robot,) if self.robot else ()) + wheels + tuple(p for p in self.live_components if p not in (tts,sink)))

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
            if self.settings.camera_enabled and self.settings.place_recognition_enabled and not (self.paths.root / 'models/places/mobileclip-s2.onnx').is_file():
                missing.append('place_model_file')
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

    def live(self, feed=None):
        """Run Rex until shutdown. feed: the optional dashboard's trace display (it wraps the
        terminal console); it is handed the composed owners just before listening starts."""
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
            trace = Trace(session_id, log_path, display=feed if feed is not None else LiveConsole())
            trace.emit("starting", log_path=str(log_path))
            if feed is not None:
                from .gui.feed import LiveParts   # Early: the avatar mirrors the wake choreography.
                feed.attach(LiveParts(settings=self.settings, owner=self.owner, robot=self.robot, leds=self.leds,
                                      log_path=str(log_path)))
        except BaseException:
            self.hardware_handoff.close()
            lease.close()
            raise
        asr, vad, encoder, brain, tts, sink, mic, *optional = self.live_components
        brain.trace = trace
        camera = next((p for p in optional if p.capability.name=='camera'),None)
        public = next((p for p in optional if p.capability.name=='public_observations'),None)
        weather = next((p for p in optional if p.capability.name=='weather_lookup'),None)
        scene = next((p for p in optional if p.capability.name=='scene_observations'),None)
        objects = next((p for p in optional if p.capability.name=='objects'),None)
        places = next((p for p in optional if p.capability.name=='places'),None)
        for provider in (public,scene,objects,places):
            if provider:provider.trace=trace
        if camera:
            camera.trace = trace
        memory = Memory(self.paths.root / "core.sqlite3")
        if places:
            places.store = memory   # Started before the providers; rooms live in the core store.
            if self.drive:
                from .drive import DriveBase
                # No trustworthy wheel signal (None) turns the room-change freeze off, as in V2.
                places.motion = lambda: self.drive.motion_risk() if self.drive.state == 'ready' else None
                places.heading = lambda: DriveBase._heading(self.drive.link.snapshot()[0] or {})
                self.drive.on_motion.append(places.note_motion)   # Between slower settled samples too.
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
            # Only playback, the microphone arm or the wheels invalidate a bearing; talking
            # head sway and the other arms do not move the array (September 27: 0 samples).
            direction.echo_risk = lambda: (output.echo_risk() or
                (self.robot is not None and self.robot.owner.microphone_moving()) or
                (self.drive is not None and self.drive.motion_risk()))
            if self.robot:
                self.robot.owner.on_motion = direction.invalidate
            if self.drive:
                self.drive.on_motion.append(lambda plan: direction.invalidate())
        runtime = None
        if self.robot:
            # Install before startup/ready speech so those frames also require settling.
            def camera_motion(moving, request_id, kind):
                if runtime:runtime.arrivals.note_base_motion(trace.clock())
                if scene:scene.note_motion(moving,request_id,'head:'+kind)
            self.robot.owner.on_camera_motion = camera_motion
        success = False
        import signal
        import threading
        def terminate(*_):
            raise KeyboardInterrupt()
        # With the dashboard, Qt owns the main thread and routes SIGTERM to a shutdown request.
        main_thread = threading.current_thread() is threading.main_thread()
        previous_sigterm = signal.signal(signal.SIGTERM,terminate) if main_thread else None
        try:
            memory.start()
            if camera:
                from .adapters.face import NAMESPACE as FACE_NAMESPACE
                camera.session_id = session_id
                camera.profiles = lambda: memory.face_profiles(FACE_NAMESPACE)
            from .startup_notice import announce
            self.startup_audio.trace = trace
            def failed_notice():
                self.startup_audio.stop()
                announce(self.settings,self.owner,output,trace,failed=True)
            if not self.owner.start(on_required_failure=failed_notice):
                trace.emit("startup_failed", report=self.doctor())
            else:
                trace.emit("startup", report=self.doctor(), evidence="live", models=[p.capability.model for p in self.live_components if p.capability.model])
                active = {r.capability for r in self.owner.results if r.operation=='start' and r.outcome=='ok'}
                camera = camera if 'camera' in active else None
                scene = scene if 'scene_observations' in active else None
                objects = objects if 'objects' in active else None
                places = places if 'places' in active else None
                keywords = next((p for p in optional if p.capability.name=='spoken_keywords' and 'spoken_keywords' in active),None)
                output.inspection = inspection if 'current_view' in active else None
                self.startup_audio.stop()   # Cut the filler; readiness never waits for it.
                # The ready line gets the usual talking motion and speaking lights.
                output.robot = self.robot if self.robot and 'robot_servos' in active else None
                if output.robot:
                    from .physical_attention import PhysicalAttention, PhysicalGesture
                    output.attention, output.gestures = PhysicalAttention(output.robot), PhysicalGesture(output.robot)
                    output.attention.start();output.gestures.start()
                output.speech_motion = output.robot.talking if output.robot else None
                output.leds = self.leds if self.leds and 'robot_leds' in active else None
                try:
                    ready = self.startup_audio.ready(Cancellation())
                except Exception as exc:
                    ready = None
                    trace.emit('startup_audio',status='ready_line_failed',error_type=type(exc).__name__)
                startup_delivery = announce(self.settings,self.owner,output,trace,ready=ready)
                mic.discard_pending()
                from .sentry import canned_lines
                # Uncached lines, paced, never over speech; sentry mode's when it can run (camera).
                self.startup_audio.warm(busy=output.active.is_set, extra=canned_lines() if camera else ())
                if self.drive and 'drive_base' not in active:
                    # Configured wheels that failed to start: Rex still knows he has them.
                    failure = next((r for r in self.owner.results if r.capability=='drive_base' and r.operation=='start'),None)
                    output.drive_unavailable = 'wheel_controller_not_connected' + (':'+failure.reason if failure and failure.reason else '')
                if self.drive and 'drive_base' in active:
                    output.drive = self.drive
                    from .motion_sounds import MotionSounds
                    self.drive.sounds = MotionSounds(sink, trace)   # Plays only while the output owner runs a drive.
                    self.drive.servos = self.robot if self.robot and 'robot_servos' in active else None
                    self.drive.faces = camera.latest if camera else None
                    self.drive.objects = objects.snapshot if objects else None
                    # After motion every track restarts; re-finding a target cannot wait two samples.
                    self.drive.object_tracks = (lambda: objects.snapshot(min_samples=1)) if objects else None
                    finder = next((p for p in optional if p.capability.name=='visual_locator'),None)
                    if finder and camera and 'visual_locator' in active:
                        from .looking import fresh_look
                        finder.trace = trace
                        self.drive.locator = finder.locate
                        self.drive.router = finder.route   # Looks at the floor to get around what blocks an approach.
                        self.drive.look = lambda after: fresh_look(camera, after)
                state = CoreState(memory,SpeakerTracker(NAMESPACE),trace, face_invalidator=camera.invalidate if camera else None,
                                  require_microphone_hold=self.robot is not None)
                state.observations,state.scene_observer=public,scene
                # Pride and AAVE modes (October 25): the voice, and for pride the lights and talking body,
                # follow the state owner's modes; reads only, nothing here switches one.
                if hasattr(tts, 'modes'):
                    tts.modes = lambda: state.voice_modes.active(trace.clock())
                pride = lambda: state.voice_modes.on('pride', trace.clock())
                if self.leds:
                    self.leds.pride = pride
                if self.robot:
                    self.robot.talking.flair = pride
                state.weather = weather if weather and 'weather_lookup' in active else None
                if self.drive:
                    from .systems import report
                    offset = self.settings.room_temperature_offset_f
                    # Configured wheels that failed to start still answer: not connected.
                    state.systems_status = ((lambda: report(self.drive.base_status(), offset)) if 'drive_base' in active
                                            else (lambda: report(None, offset)))
                state.places=places
                if objects:
                    from .photo_memory import PhotoMemory
                    state.objects=objects
                    state.photo_memory=PhotoMemory(self.paths.root / 'visual-memory')
                    state.photo_memory.start()
                perception = Perception(asr,encoder,trace,camera,direction)
                session = Session(session_id, brain, output, trace, state=state)
                if self.robot and camera:
                    from .gaze import FaceTurns, addressee_neck
                    from .faces import VISIBLE_FACE_MAX_AGE
                    import random
                    turns = FaceTurns(random.Random(), trace.clock)
                    def gaze():
                        now = trace.clock()
                        faces = [f for f in camera.latest(now) if 0 <= now-f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('box')]
                        current = state.current
                        speaker = current[4].person_id if current else None
                        direction = getattr(current[3], 'direction', None) if current else None
                        return addressee_neck(faces, self.robot.owner.neck_degrees(), speaker, direction)
                    self.robot.talking.gaze = gaze
                    def idle_face():
                        now = trace.clock()
                        faces = [f for f in camera.latest(now) if 0 <= now-f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('box')]
                        current = state.current
                        # The latest identified speaker (voice or the state owner's inference) draws
                        # the gaze for a few seconds; aiming only, never attribution.
                        speaker = ((current[4].speaker_id, current[1].ended_at)
                                   if current and current[4].speaker_id else None)
                        return turns(faces, self.robot.owner.neck_degrees(), speaker)
                    self.robot.idle.face = idle_face
                    def reflex_faces():
                        now = trace.clock()
                        return [f['box'] for f in camera.latest(now) if 0 <= now-f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('box')]
                    self.robot.reflex.faces = reflex_faces
                agency = next((p for p in optional if p.capability.name=='agency_planner'),None)
                if agency and 'agency_planner' in active:
                    agency.trace = trace
                    session.agency_planner = agency
                writer = next((p for p in optional if p.capability.name=='joke_writer'),None)
                if writer and 'joke_writer' in active:
                    from .jokes import JokeRoom
                    writer.trace = trace
                    session.joke_room = JokeRoom(writer, trace, trace.clock)
                if self.cloner and 'voice_cloner' in active:
                    from .impersonation import ReferenceStore
                    self.cloner.trace = trace
                    output.cloner = self.cloner
                    state.voice_references = ReferenceStore(self.paths.root / 'cache' / 'voice_refs')
                elif self.cloner:
                    failure = next((r for r in self.owner.results if r.capability=='voice_cloner' and r.operation=='start'),None)
                    trace.emit('impersonation_unavailable',reason=failure.reason if failure and failure.reason else (failure.error_type if failure else 'not_started'))
                if startup_delivery and startup_delivery.completed_text:
                    session.history.append({'role':'assistant','content':' '.join(startup_delivery.completed_text)})
                if self.settings.camera_enabled and camera is None:
                    session.history.append({'role':'developer','content':
                        'Camera unavailable at startup. No current visual evidence is available this run; do not claim to see.'})
                recorder = CaptureRecorder(self.paths.root / "recordings",session_id) if self.settings.record_evaluation else None
                runtime = VoiceRuntime(mic, vad, asr, session, trace, self.settings.end_silence_seconds,perception,recorder,
                                       lull_seconds=self.settings.proactive_lull_seconds,observations=public,scene_observer=scene,robot=self.robot,
                                       drive=output.drive,keywords=keywords)
                if camera:
                    # Sentry mode (owner, October 22) needs the camera: faces trip and stand it down.
                    from .push import Push, valid_topic
                    from .sentry_guard import SentryGuard
                    from .sentry_recording import SentryRecorder
                    topic = Secrets.load(self.paths.root).ntfy_topic
                    push = Push(topic, trace) if valid_topic(topic) else None
                    if topic and push is None:
                        trace.emit('push_notification', outcome='invalid_topic')
                    guard = SentryGuard(session, output, trace, camera=camera, canned=self.startup_audio.speech,
                        recorder=SentryRecorder(self.paths.root / 'sentry' / 'recordings', camera.hub.peek, trace),
                        push=push, observations=public,
                        shutdown=lambda reason: runtime.request_shutdown('sentry', reason=reason))
                    session.on_sentry = guard.start
                    runtime.sentry = guard
                    trace.emit('sentry', status='available', push_alerts=push is not None,
                               recordings=str(self.paths.root / 'sentry' / 'recordings'))
                    if output.drive:
                        output.drive.hold = guard.holding_wheels   # The wheels never move while he guards.
                if scene:
                    scene.motion_risk = lambda: bool((output.robot and output.robot.owner.camera_moving) or
                                                     (output.drive and output.drive.motion_risk()))
                    if output.drive:
                        output.drive.on_motion.append(lambda plan: scene.note_motion(
                            output.drive.moving.is_set(),plan.request_id if plan else 'uncommanded','base'))
                if output.drive:
                    output.drive.on_motion.append(lambda plan: runtime.arrivals.note_base_motion(
                        trace.clock(), allow_new=bool(plan and plan.allow_new_arrivals)))
                if feed is not None:
                    from .gui.feed import LiveParts
                    feed.attach(LiveParts(runtime=runtime, settings=self.settings, owner=self.owner, memory=memory,
                        camera=camera, objects=objects, places=places, robot=output.robot, leds=output.leds,
                        drive=output.drive, radar=self.radar,
                        sink=sink, log_path=str(log_path)))
                runtime.run()
                success = True
        finally:
            if runtime and (any(w.is_alive() for w in runtime.workers) or
                            (runtime.session.output_worker is not None and runtime.session.output_worker.is_alive())):
                # Do not unload native models under an unfinished worker, but still stop the
                # wheels, park the servos and fade the lights: they never wait on those workers.
                if self.robot:
                    self.robot.on_shutdown = lambda: None   # A stuck output worker may still own the speaker.
                self.owner.close_only({'drive_base','radar','robot_servos','robot_leds'})
                trace.emit("shutdown_failed", reason="active_worker_resources_retained_until_process_exit",
                           hardware_released=[r.capability for r in self.owner.results if r.operation=='close'])
                self.workers_retained = True   # The caller ends the process now; nothing waits for them.
            else:
                self.close()
                if runtime and runtime.session.state.photo_memory:runtime.session.state.photo_memory.close()
                if runtime and runtime.session.state.voice_references:runtime.session.state.voice_references.close()
                memory.close()
                trace.emit("shutdown", report=self.doctor())
                # Only acknowledge handoff after device closure, including failed
                # startup cleanup. A stuck/failed closer retains the leases.
                if self.owner.state == 'closed':
                    self.hardware_handoff.close()
            trace.close()
            if self.owner.state == 'closed':
                lease.close()
            if main_thread:
                signal.signal(signal.SIGTERM,previous_sigterm)
        return success and self.owner.state == "closed"
