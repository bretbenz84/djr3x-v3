"""Explicit one-time import of authorized device settings, credentials, and ASR weights.

Never executes v2 Python. No database, personal audio, or voiceprint imports.
"""
import ast
import hashlib
import json
from pathlib import Path
import shlex
import shutil

from r3x.settings import ScratchPaths


def literals(path, wanted):
    result = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    result[target.id] = ast.literal_eval(node.value)
    return result


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    source = Path("/Users/bbenziger/djr3x-v2")
    root = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    root.prepare()
    if any((root.root / name).exists() for name in ("settings.toml", "secrets.toml", "v2-setup-import.json")):
        raise SystemExit("Local setup already exists; no files were replaced")
    wanted = {"AUDIO_DEVICE_NAME", "AUDIO_DEVICE_INDEX", "CAMERA_DEVICE_NAME", "CAMERA_INDEX"}
    values = {}
    for line in (source / ".env").read_text().splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() in wanted:
            parts = shlex.split(value, comments=True)
            values[key.strip()] = " ".join(parts)
    keys = literals(source / "apikeys.py", {"OPENAI_API_KEY", "ELEVENLABS_API_KEY"})
    # Rex's voice is in the repository (r3x.voice_modes.REX_VOICE, October 25), not imported.
    if not all(isinstance(keys.get(k), str) and keys[k] for k in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY")):
        raise SystemExit("Required literal credentials unavailable")
    settings = {"profile": "dev-mac"}
    if values.get("AUDIO_DEVICE_NAME"):
        settings["input_device_name"] = values["AUDIO_DEVICE_NAME"]
    if values.get("AUDIO_DEVICE_INDEX"):
        settings["input_device_index"] = int(values["AUDIO_DEVICE_INDEX"])
    if values.get("CAMERA_DEVICE_NAME"):
        settings["camera_device_name"] = values["CAMERA_DEVICE_NAME"]
    settings["camera_enabled"] = False
    for name, mapping in (("settings.toml", settings), ("secrets.toml", {k.lower(): v for k, v in keys.items()})):
        path = root.root / name
        with path.open("x") as handle:
            path.chmod(0o600)
            handle.write("\n".join(f"{k} = {json.dumps(v)}" for k, v in mapping.items()) + "\n")
    source_model = source / "assets/models/qwen_asr/Qwen3-ASR-1.7B-8bit"
    target_model = root.root / "models/qwen3-asr"
    target_model.mkdir(parents=True, exist_ok=False)
    records = []
    for path in sorted(source_model.iterdir()):
        if path.is_file() and path.suffix in {".json", ".txt", ".safetensors"}:
            destination = target_model / path.name
            shutil.copyfile(path, destination)  # Independent files, no symlink/hardlink.
            source_digest, target_digest = digest(path), digest(destination)
            if source_digest != target_digest:
                raise SystemExit("Model-copy digest mismatch")
            records.append({"file": path.name, "sha256": source_digest})
    provenance = {"source": str(source), "model_files": records,
                  "camera_selection_deferred": {k: v for k, v in values.items() if k.startswith("CAMERA_")},
                  "config_source_digests": {n: digest(source / n) for n in (".env", "apikeys.py", "config.py")}}
    path = root.root / "v2-setup-import.json"
    with path.open("x") as handle:
        path.chmod(0o600)
        json.dump(provenance, handle, indent=2)
    print("Copied selected settings, credentials, and verified ASR model files to ignored local/. No values displayed.")


if __name__ == "__main__":
    main()
