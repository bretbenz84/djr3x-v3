"""Strict, consumed core settings; credentials live separately in ignored storage."""
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
    microphone_mode: str = "standard"
    input_gain: float = 1.0
    direction_enabled: bool = False
    servo_port: str = ""  # Empty disables physical servos; explicit device path opts in.
    echo_isolated: bool = False
    vad_threshold: float = 0.5
    end_silence_seconds: float = 1.2
    asr_trust_logprob: float = -0.35
    camera_enabled: bool = False
    camera_device_name: str = ""
    camera_width: int = 1280
    camera_height: int = 720
    camera_framerate: float = 30.0
    record_evaluation: bool = False
    proactive_lull_seconds: float = 8.0
    scene_observations_enabled: bool = True
    object_memory_enabled: bool = False
    news_feed_urls: tuple[str, ...] = ("https://feeds.bbci.co.uk/news/rss.xml",)
    weather_enabled: bool = True
    weather_location: str = "Sacramento, CA"
    weather_latitude: float = 38.5816
    weather_longitude: float = -121.4944

    def __post_init__(self) -> None:
        if self.profile != "dev-mac":
            raise ValueError("Only the dev-mac boot profile is implemented")
        if self.microphone_mode not in ("standard", "respeaker-flex"):
            raise ValueError("Unknown microphone_mode")
        if self.direction_enabled and self.microphone_mode != "respeaker-flex":
            raise ValueError("Direction requires the ReSpeaker Flex microphone")
        for name in ("input_device_name", "output_device_name", "camera_device_name", "servo_port"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be text")
        if self.servo_port and (not self.servo_port.startswith('/dev/cu.') or
                                any(c.isspace() for c in self.servo_port) or
                                '..' in self.servo_port or '/' in self.servo_port[5:]):
            raise ValueError('servo_port must be an explicit macOS /dev/cu. device')
        if self.input_device_index is not None and (type(self.input_device_index) is not int or self.input_device_index < 0):
            raise ValueError("input_device_index must be a nonnegative integer")
        for name in ("echo_isolated","camera_enabled","record_evaluation","scene_observations_enabled","weather_enabled","direction_enabled","object_memory_enabled"):
            if type(getattr(self,name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        from .observations import https_url
        if not isinstance(self.news_feed_urls,(list,tuple)) or len(self.news_feed_urls)>4:
            raise ValueError('AtMostFourNewsFeeds')
        for url in self.news_feed_urls:
            if not isinstance(url,str):raise ValueError('InvalidNewsFeed')
            https_url(url)
        object.__setattr__(self,'news_feed_urls',tuple(self.news_feed_urls))
        if not isinstance(self.weather_location,str) or not 0<len(self.weather_location)<=120:raise ValueError('WeatherLocationRequired')
        for name,limit in (('weather_latitude',90),('weather_longitude',180)):
            value=getattr(self,name)
            if type(value) not in (int,float) or not math.isfinite(value) or not -limit<=value<=limit:raise ValueError('InvalidWeatherCoordinates')
        if self.camera_enabled and not self.camera_device_name.strip():
            raise ValueError("Enabled camera requires a device name")
        for name, lower, upper in (("camera_width", 320, 1920), ("camera_height", 240, 1080)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper or value % 2:
                raise ValueError(f"Invalid {name}")
        for name, lower, upper in (("vad_threshold", .01, .99), ("end_silence_seconds", .2, 2),
                                   ("asr_trust_logprob", -5, 0), ("proactive_lull_seconds", 3, 120),
                                   ("camera_framerate", 1, 120.2), ("input_gain", .1, 4)):
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
        paths = [self.root, *(self.root / name for name in ("sessions", "recordings", "cache", "validation"))]
        for path in paths:
            if path.resolve() != path:
                raise ValueError("Scratch paths must not contain symlinks")
        for path in paths:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
