"""Explicit composition of the complete core; no hardware starts during inspection."""
from dataclasses import asdict
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
        camera = Camera(self.settings.camera_device_name,self.paths.root / "models/haarcascade_frontalface_default.xml") if self.settings.camera_enabled else None
        self.live_components = (asr, vad, encoder, brain, tts, sink, mic) + ((camera,) if camera else ())
        self.owner = ProviderOwner(self.live_components)

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
                if not (self.paths.root / "models/haarcascade_frontalface_default.xml").is_file():
                    missing.append("face_detector_file")
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
                            "Face presence carries no biometric identity or speaker vote",
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
        session_id = uuid4().hex
        try:
            log_path = self.paths.root / "sessions" / (session_id + ".jsonl")
            trace = Trace(session_id, log_path, display=LiveConsole())
            trace.emit("starting", log_path=str(log_path))
        except BaseException:
            lease.close()
            raise
        asr, vad, encoder, brain, tts, sink, mic, *optional = self.live_components
        camera = optional[0] if optional else None
        if camera:
            camera.trace = trace
        memory = Memory(self.paths.root / "core.sqlite3")
        output = OutputOwner(tts, sink, trace)
        mic.echo_risk = output.echo_risk
        self.owner = ProviderOwner(self.live_components)
        runtime = None
        success = False
        try:
            memory.start()
            if not self.owner.start():
                trace.emit("startup_failed", report=self.doctor())
            else:
                trace.emit("startup", report=self.doctor(), evidence="live", models=[p.capability.model for p in self.live_components if p.capability.model])
                state = CoreState(memory,SpeakerTracker(NAMESPACE),trace)
                perception = Perception(asr,encoder,trace,camera)
                session = Session(session_id, brain, output, trace, state=state)
                recorder = CaptureRecorder(self.paths.root / "recordings",session_id) if self.settings.record_evaluation else None
                runtime = VoiceRuntime(mic, vad, asr, session, trace, self.settings.end_silence_seconds,perception,recorder)
                runtime.run()
                success = True
        finally:
            if runtime and any(w.is_alive() for w in runtime.workers):
                # Do not unload native models under an unfinished worker.
                trace.emit("shutdown_failed", reason="active_worker_resources_retained_until_process_exit")
            else:
                self.close()
                memory.close()
                trace.emit("shutdown", report=self.doctor())
            trace.close()
            lease.close()
        return success and self.owner.state == "closed"
