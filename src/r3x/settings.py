"""Only settings consumed by the independent-boot slice."""
from dataclasses import dataclass, fields, field
from pathlib import Path
import math
import os
import tomllib


@dataclass(frozen=True)
class Settings:
    profile: str = "dev-mac"
    input_device_name: str = ""
    input_device_index: int | None = None
    output_device_name: str = ""
    echo_isolated: bool = False
    vad_threshold: float = 0.5
    end_silence_seconds: float = 0.6
    asr_trust_logprob: float = -0.35

    def __post_init__(self) -> None:
        if self.profile != "dev-mac":
            raise ValueError("Only the dev-mac boot profile is implemented")
        for name in ("input_device_name", "output_device_name"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
        if self.input_device_index is not None and (type(self.input_device_index) is not int or self.input_device_index < 0):
            raise ValueError("input_device_index must be a nonnegative integer")
        if type(self.echo_isolated) is not bool:
            raise ValueError("echo_isolated must be boolean")
        for name, lower, upper in (("vad_threshold", .01, .99), ("end_silence_seconds", .2, 2),
                                   ("asr_trust_logprob", -5, 0)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"Invalid {name}")

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with path.open("rb") as handle:
            values = tomllib.load(handle)
        unknown = values.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        return cls(**values)


@dataclass(frozen=True)
class Secrets:
    openai_api_key: str = field(default="", repr=False)
    elevenlabs_api_key: str = field(default="", repr=False)
    elevenlabs_voice_id: str = field(default="", repr=False)

    @classmethod
    def load(cls, root: Path) -> "Secrets":
        path = root / "secrets.toml"
        if path.resolve() != path:
            raise ValueError("Secrets must be an independent local file")
        values = tomllib.loads(path.read_text()) if path.exists() else {}
        if values.keys() - {f.name for f in fields(cls)}:
            raise ValueError("Unknown secret setting")
        for f in fields(cls):
            values[f.name] = os.environ.get(f.name.upper(), values.get(f.name, ""))
            if not isinstance(values[f.name], str):
                raise ValueError("Credentials must be strings")
        return cls(**values)

    def readiness(self) -> dict[str, bool]:
        return {f.name: bool(getattr(self, f.name).strip()) for f in fields(self)}


@dataclass(frozen=True)
class ScratchPaths:
    """All mutable runtime artifacts stay under this checkout's local directory."""
    root: Path

    @classmethod
    def for_project(cls, project: Path) -> "ScratchPaths":
        project = project.resolve(strict=True)
        root = project / "local"
        if root.resolve() != root:
            raise ValueError("Scratch root must not be a symlink")
        return cls(root)

    def prepare(self) -> None:
        # Recheck at the write boundary, including pre-existing child links.
        paths = [self.root, *(self.root / name for name in ("sessions", "recordings", "cache"))]
        for path in paths:
            if path.resolve() != path:
                raise ValueError("Scratch paths must not contain symlinks")
        for path in paths:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
