"""Independent boot; real adapters will be composed here in slice 1."""
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
        secrets = Secrets.load(self.paths.root)
        asr = QwenASR(self.paths.root / "models/qwen3-asr", self.settings.asr_trust_logprob)
        vad = SileroVAD(self.settings.vad_threshold)
        brain = OpenAIBrain(secrets.openai_api_key)
        tts = ElevenLabsTTS(secrets.elevenlabs_api_key, secrets.elevenlabs_voice_id)
        sink = AudioSink(self.settings.output_device_name)
        mic = Microphone(self.settings, lambda: False)
        self.live_components = (asr, vad, brain, tts, sink, mic)
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
            for provider in self.owner.providers:
                missing.extend(name for name, version in dependency_status(provider.capability).items()
                               if version is None)
        return sorted(set(missing))

    def doctor(self) -> dict:
        return {
            "profile": self.settings.profile,
            "python": platform.python_version(),
            "slice": "1-audible-loop" if self.live_components else "0-independent-boot",
            "conversation_ready": bool(self.live_components and self.owner.state == "ready"),
            "missing": self.readiness(),
            "scratch_root": str(self.paths.root),
            "lifecycle": self.owner.state,
            "capabilities": [
                {**asdict(p.capability), "dependencies": dependency_status(p.capability)}
                for p in self.owner.providers
            ],
            "results": [asdict(result) for result in self.owner.results],
            "limitations": ["Speaker identity, camera, and persistent memory are not implemented",
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
        from .lease import LiveLease
        self.configure_live()
        if self.readiness():
            return False
        self.paths.prepare()
        lease = LiveLease(self.paths.root / "sessions/live.lock")
        lease.acquire()
        session_id = uuid4().hex
        try:
            trace = Trace(session_id, self.paths.root / "sessions" / (session_id + ".jsonl"))
        except BaseException:
            lease.close()
            raise
        asr, vad, brain, tts, sink, mic = self.live_components
        output = OutputOwner(tts, sink, trace)
        mic.echo_risk = output.echo_risk
        self.owner = ProviderOwner(self.live_components)
        runtime = None
        success = False
        try:
            if not self.owner.start():
                trace.emit("startup_failed", report=self.doctor())
            else:
                trace.emit("startup", report=self.doctor(), evidence="live", models=[asr.capability.model, vad.capability.model])
                session = Session(session_id, brain, output, trace)
                runtime = VoiceRuntime(mic, vad, asr, session, trace, self.settings.end_silence_seconds)
                runtime.run()
                success = True
        finally:
            if runtime and any(w.is_alive() for w in runtime.workers):
                # Do not unload native models under an unfinished worker.
                trace.emit("shutdown_failed", reason="active_worker_resources_retained_until_process_exit")
            else:
                self.close()
                trace.emit("shutdown", report=self.doctor())
            trace.close()
            lease.close()
        return success and self.owner.state == "closed"
