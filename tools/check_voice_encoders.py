"""ERes2NetV2 against CAM++ on Rex's microphone: time, CPU and memory per line, and telling voices apart.

  prepare      download the pinned ERes2NetV2 model into local/models (about 68 MB, checksum verified)
  live         take the microphone from the wake supervisor (nothing moves), make a voice print with
               each encoder from the same read sentences, then run both on prompted short and normal
               lines; the camera's face detection runs as in a conversation unless --no-camera
  saved DIR    re-score a live run's saved lines offline

Both encoders get the same features and 2 s windows as Rex (adapters/voice.py); CAM++ is Rex's own.
Order alternates per line. Clips, prints and logs stay in local/validation; Rex's store is never written.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import resource
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from uuid import uuid4
import wave

from r3x.adapters.voice import CAMPlus
from r3x.contracts import Cancellation, Capture, ProviderFailure
from r3x.dataset import read_wav, write_wav
from r3x.settings import ScratchPaths, Settings
from r3x.speakers import SpeakerTracker


ERES_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
            "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx")
# The Hugging Face mirror (csukuangfj/speaker-embedding-models) lists this digest; its CAM++ digest is Rex's pin.
ERES_SHA256 = "bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239"
ERES_BYTES = 71441526


class ERes2NetV2(CAMPlus):
    title, sha256 = "ERes2NetV2 ONNX", ERES_SHA256
    namespace = "eres2netv2:" + ERES_SHA256[:12] + ":16k-fbank80-globalmean:192:v1"


ENROLL = (
    "Hey Rex, it's {name}. I'm reading a few sentences so you can learn my voice.",
    "The quick brown fox jumps over the lazy dog, then takes a long nap in the sun.",
    "This morning I made coffee, checked the weather, and found out it's going to rain again.",
    "My favorite thing about summer is the river, when the evenings finally cool down.",
    "If you roll into the kitchen one more time, I'm taking your wheels away for a week.",
    "Tell me something interesting about the stars we can see from the backyard tonight.",
)
SHORT = ("Yes.", "No.", "Okay.", "Hey Rex.", "Stop.", "What?", "Thanks.", "Good morning.", "Look at me.",
         "Turn left.", "I don't know.", "Sure, why not.", "Come here, Rex.", "That's funny.", "Not now.", "Hi, it's me.")
NORMAL = (
    "What's the weather going to be like this weekend?",
    "I was thinking we could watch a movie later, maybe something with spaceships.",
    "Can you look over at the couch and tell me if the dog is still asleep?",
    "Someone's coming over tomorrow, so try to be on your best behavior.",
    "I spent most of the afternoon fixing the garage door, and it still squeaks.",
    "Do you remember what I told you about my trip last month?",
    "Rex, that was the worst joke I've heard all week, and I loved it.",
    "Let's play a game of Jeopardy after dinner if everyone's up for it.",
)
OWN_WORDS = 6
ENROLL_VOICED = 2.     # Rex's own minimum for an enrollment sample.
BUCKETS = ((0, 1, "under 1 s"), (1, 2, "1-2 s"), (2, 4, "2-4 s"), (4, 1e9, "4 s or more"))


class Machine:
    """Whole-Mac CPU ticks (all cores, mach host_statistics) and this process's CPU time; stdlib only."""
    def __init__(self):
        import ctypes
        self.ctypes = ctypes
        self.lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.lib.mach_host_self.restype = ctypes.c_uint32
        self.host = self.lib.mach_host_self()
        self.cores = os.cpu_count()

    def ticks(self):
        info, count = (self.ctypes.c_uint32*4)(), self.ctypes.c_uint32(4)
        if self.lib.host_statistics(self.host, 3, info, self.ctypes.byref(count)):   # HOST_CPU_LOAD_INFO
            raise OSError("host_statistics")
        return tuple(info)   # user, system, idle, nice

    def busy_cores(self, before, after):
        user, system, idle, nice = ((b-a) % 2**32 for a, b in zip(before, after))
        total = user+system+idle+nice
        return (user+system+nice)/total*self.cores if total else None

    @staticmethod
    def cpu():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_utime + usage.ru_stime

    @staticmethod
    def rss_mb():
        out = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=2).stdout
        return int(out.strip())/1024


def measured(machine, call):
    ticks, cpu, began = machine.ticks(), machine.cpu(), time.perf_counter()
    result = call()
    seconds = time.perf_counter()-began
    return result, {"seconds": seconds, "cpu_seconds": machine.cpu()-cpu,
                    "machine_busy_cores": machine.busy_cores(ticks, machine.ticks())}


class Log:
    def __init__(self, path):
        self.handle, self.lock = path.open("x"), threading.Lock()

    def emit(self, event, **fields):
        with self.lock:
            self.handle.write(json.dumps({"event": event, "at": time.time(), **fields}) + "\n")
            self.handle.flush()


class Sampler(threading.Thread):
    """Once a second: how busy the whole Mac and this process were, and what the test was doing."""
    def __init__(self, machine, log):
        super().__init__(name="cpu-sampler", daemon=True)
        self.machine, self.log, self.activity, self.stop = machine, log, "starting", threading.Event()
        self.samples = []

    def run(self):
        ticks, cpu, at = self.machine.ticks(), self.machine.cpu(), time.perf_counter()
        while not self.stop.wait(1.):
            now_ticks, now_cpu, now = self.machine.ticks(), self.machine.cpu(), time.perf_counter()
            sample = {"activity": self.activity,
                      "machine_busy_percent": 100*self.machine.busy_cores(ticks, now_ticks)/self.machine.cores,
                      "process_cpu_percent": 100*(now_cpu-cpu)/(now-at)}
            self.samples.append(sample)
            self.log.emit("cpu", **sample)
            ticks, cpu, at = now_ticks, now_cpu, now


def vad_capture(pcm, vad, session_id):
    """One capture over the whole PCM, voiced where Silero says so (Rex's 512-sample frames)."""
    vad.reset()
    intervals = []
    for n in range(0, len(pcm)-1023, 1024):
        if vad.is_speech(pcm[n:n+1024]):
            a, b = n/32000, (n+1024)/32000
            if intervals and abs(intervals[-1][1]-a) < .002:
                intervals[-1] = (intervals[-1][0], b)
            else:
                intervals.append((a, b))
    return Capture(uuid4().hex, session_id, pcm, 0., intervals[-1][1] if intervals else 0., len(pcm)/32000,
                   voiced_intervals=tuple(intervals))


def analyze_both(encoders, capture, n, machine, sampler=None):
    results = {}
    for name in (list(encoders) if n % 2 else list(encoders)[::-1]):
        if sampler:
            sampler.activity = name
        results[name] = measured(machine, lambda e=encoders[name]: e.analyze(capture, Cancellation()))
    if sampler:
        sampler.activity = "listening"
    return results


def famous_prints(root, encoders, vad):
    """Rival prints from the public reference voices (recorded elsewhere, so easier to tell apart)."""
    import numpy as np
    from scipy.signal import resample_poly
    prints = {name: {} for name in encoders}
    for path in sorted((root/"assets/voices/famous").glob("*.wav")):
        if path.stem == "star-trek-computer":
            continue
        with wave.open(str(path), "rb") as handle:
            rate, pcm = handle.getframerate(), handle.readframes(handle.getnframes())
        if rate not in (16000, 24000):
            continue
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if rate == 24000:
            samples = resample_poly(samples, 2, 3)
        pcm = np.clip(samples, -32768, 32767).astype("<i2").tobytes()
        third = len(pcm)//6*2
        for k in range(3):
            capture = vad_capture(pcm[k*third:(k+1)*third], vad, "famous")
            for name, encoder in encoders.items():
                observation = encoder.analyze(capture, Cancellation())
                if observation.embedding:
                    prints[name].setdefault("famous:" + path.stem, []).append(observation.embedding)
    return prints


def bucket(voiced):
    return next(label for low, high, label in BUCKETS if low <= voiced < high)


def spread(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return {"n": len(values), "median": statistics.median(values),
            "p90": values[min(len(values)-1, int(.9*len(values)))], "max": values[-1]}


def auc(targets, rivals):
    if not targets or not rivals:
        return None
    return sum((t > r) + .5*(t == r) for t in targets for r in rivals)/(len(targets)*len(rivals))


def summarize(clips, encoders, rivals, load, samples):
    """clips: dicts with phase, speaker, trusted, voiced_seconds, truncated and results {encoder: (observation, cost)}."""
    listening = [s for s in samples if s["activity"] == "listening"]
    # The process's own background while listening (camera face detection, microphone, VAD), in cores;
    # it is taken out of each line's CPU time so the encoder's share remains.
    background = statistics.median(s["process_cpu_percent"] for s in listening)/100 if listening else 0.
    summary = {"encoders": {}, "cores": os.cpu_count(), "background_process_cores": background,
               "listening_machine_busy_percent": spread(s["machine_busy_percent"] for s in listening)}
    for name, encoder in encoders.items():
        costs = [c["results"][name][1] for c in clips]
        timing = {"all": spread(c["seconds"] for c in costs),
                  "cpu_seconds": spread(max(0., c["cpu_seconds"]-background*c["seconds"]) for c in costs),
                  "raw_process_cpu_seconds": spread(c["cpu_seconds"] for c in costs),
                  "machine_busy_cores": spread(c["machine_busy_cores"] for c in costs),
                  "windows": spread(len(c["results"][name][0].windows) for c in clips)}
        for _, _, label in BUCKETS:
            timing[label] = spread(c["results"][name][1]["seconds"] for c in clips if bucket(c["voiced_seconds"]) == label)
        enrolled = {}
        for c in clips:
            observation = c["results"][name][0]
            if c["phase"] == "enroll" and observation.embedding:
                enrolled.setdefault(c["speaker"], []).append(observation.embedding)
        tracker, others = SpeakerTracker(encoder.namespace), rivals.get(name, {})
        rows = {}
        for c in clips:
            if c["phase"] == "enroll" or not c["trusted"] or c["speaker"] not in enrolled:
                continue
            observation = c["results"][name][0]
            for label in (bucket(c["voiced_seconds"]), "all test lines", c["speaker"] + ", all lines"):
                row = rows.setdefault(label, {"lines": 0, "no_voice_evidence": 0, "picked_right": 0, "named_right": 0,
                                              "named_wrong": 0, "not_named": 0, "own_scores": [], "rival_scores": []})
                row["lines"] += 1
                if not observation.embedding:
                    row["no_voice_evidence"] += 1
                    row["not_named"] += 1
                    continue
                own = tracker.rank(observation.embedding, {c["speaker"]: enrolled[c["speaker"]]})[0][1]
                rival = [score for _, score in tracker.rank(observation.embedding,
                         {k: v for k, v in (enrolled | others).items() if k != c["speaker"]})]
                row["own_scores"].append(own)
                row["rival_scores"].extend(rival)
                row["picked_right"] += own > max(rival, default=-1)
                # Rex's own naming rule and thresholds (set for CAM++), against the people enrolled here.
                person, _ = tracker.match(observation, enrolled, trusted=True, truncated=c["truncated"])
                row["named_right" if person == c["speaker"] else "named_wrong" if person else "not_named"] += 1
        for row in rows.values():
            row["median_own_score"] = statistics.median(row["own_scores"]) if row["own_scores"] else None
            row["auc"] = auc(row.pop("own_scores"), row.pop("rival_scores"))
        summary["encoders"][name] = {**load.get(name, {}), "timing": timing, "accuracy": rows,
                                     "voice_prints": {k: len(v) for k, v in enrolled.items()},
                                     "rival_prints": len(others)}
    names = list(encoders)
    summary["extra_seconds_per_line"] = spread(c["results"][names[1]][1]["seconds"]-c["results"][names[0]][1]["seconds"]
                                               for c in clips)
    return summary


def report(summary, folder):
    names = list(summary["encoders"])
    enc = summary["encoders"]
    def cell(value, fmt="{:.3f} s"):
        return (fmt.format(value) if value is not None else "-").ljust(14)
    def stat(name, *keys):
        value = enc[name]
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return value
    print("\n" + "="*72 + "\nRESULTS  (" + str(folder/"summary.json") + ")\n")
    print("Time to analyze one line. Rex waits for this before transcribing, so it adds to every reply.")
    print(" "*26 + "".join(n.ljust(14) for n in names))
    for title, keys in (("typical (median)", ("all", "median")), ("slow (1 in 10)", ("all", "p90")),
                        ("slowest", ("all", "max"))) + tuple((f"  {label} speech", (label, "median")) for _, _, label in BUCKETS):
        print(title.ljust(26) + "".join(cell(stat(n, "timing", *keys)) for n in names))
    extra = summary["extra_seconds_per_line"]
    if extra:
        print(f"{names[1]} extra per line: typical {extra['median']:+.3f} s, slow {extra['p90']:+.3f} s, worst {extra['max']:+.3f} s")
    print("\nCPU and memory")
    print("CPU time per line".ljust(26) + "".join(cell(stat(n, "timing", "cpu_seconds", "median")) for n in names))
    print("Mac cores busy during it".ljust(26) + "".join(cell(stat(n, "timing", "machine_busy_cores", "median"), "{:.1f} of " + str(summary["cores"])) for n in names))
    print("memory to load".ljust(26) + "".join(cell(stat(n, "memory_mb"), "+{:.0f} MB") for n in names))
    print("load time".ljust(26) + "".join(cell(stat(n, "load_seconds"), "{:.2f} s") for n in names))
    print("first use after loading".ljust(26) + "".join(cell(stat(n, "first_use_seconds"), "{:.2f} s") for n in names))
    listening = summary["listening_machine_busy_percent"]
    if listening:
        print(f"Whole Mac while just listening: {listening['median']:.0f}% busy typical, {listening['max']:.0f}% at most")
    print("\nTelling voices apart (lines not used for a voice print)")
    print("  right: the line sounded more like its speaker's print than any other print")
    print("  named: Rex's naming rule with today's thresholds, which were set for CAM++ (right/wrong/unsure)")
    print("  AUC: 1.00 means the speaker's own score always beat every other print's")
    if max(len(enc[n]["voice_prints"]) for n in names) < 2:
        print("  (one speaker enrolled: a wrong name needs a second person, so 'wrong' stays 0 and rivals are the famous voices)")
    speakers = sorted({k for n in names for k in enc[n]["accuracy"] if k.endswith(", all lines")})
    for label in [b[2] for b in BUCKETS] + ["all test lines"] + speakers:
        rows = [enc[n]["accuracy"].get(label) for n in names]
        if not any(rows):
            continue
        print(f"  {label} ({rows[0]['lines'] if rows[0] else 0} lines)")
        for n, row in zip(names, rows):
            if row:
                score = f"{row['auc']:.2f}" if row["auc"] is not None else "-"
                print(f"    {n.ljust(12)} right {row['picked_right']}/{row['lines']}   named {row['named_right']}/"
                      f"{row['named_wrong']}/{row['not_named']}   AUC {score}   no voice evidence {row['no_voice_evidence']}")
    print("\nRe-score these lines later:  .venv/bin/python tools/check_voice_encoders.py saved " + str(folder))


def prepare(paths):
    path = paths.root/"models/eres2netv2.onnx"
    if not path.exists():
        temporary = path.with_name(uuid4().hex + ".download")
        try:
            with urllib.request.urlopen(ERES_URL, timeout=30) as response, temporary.open("xb") as handle:
                total = 0
                while chunk := response.read(1024*1024):
                    total += len(chunk)
                    if total > ERES_BYTES:
                        raise ValueError("ModelDownloadTooLarge")
                    handle.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != ERES_SHA256:
                raise ValueError("ModelDigestMismatch")
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != ERES_SHA256:
        raise ValueError("ExistingModelDigestMismatch")
    print(json.dumps({"file": str(path), "source": ERES_URL, "sha256": ERES_SHA256, "bytes": path.stat().st_size}))


def start_encoders(paths, machine, log):
    import numpy as np
    import onnxruntime, torch, torchaudio.compliance.kaldi   # Shared libraries first: neither model is charged for them.
    encoders = {"CAM++": CAMPlus(paths.root/"models/campplus.onnx"), "ERes2NetV2": ERes2NetV2(paths.root/"models/eres2netv2.onnx")}
    noise = (np.random.default_rng(0).normal(0, 3000, 32000)).astype("<i2").tobytes()
    load = {}
    for name, encoder in encoders.items():
        before, began = machine.rss_mb(), time.perf_counter()
        encoder.start()
        loaded = time.perf_counter()
        encoder.embed(noise, Cancellation())   # First use allocates the runtime's working memory.
        load[name] = {"load_seconds": loaded-began, "first_use_seconds": time.perf_counter()-loaded,
                      "memory_mb": machine.rss_mb()-before}
        log.emit("encoder_loaded", encoder=name, namespace=encoder.namespace, **load[name])
    return encoders, load


class Listener:
    """Rex's microphone, Silero VAD and endpointing, one utterance at a time; typed lines arrive as commands."""
    def __init__(self, mic, vad, settings):
        from r3x.capture import Endpoint
        self.mic, self.vad = mic, vad
        self.endpoint = Endpoint("voice-encoders", settings.end_silence_seconds)
        self.commands = Queue()
        threading.Thread(target=self._stdin, name="stdin", daemon=True).start()

    def _stdin(self):
        for line in sys.stdin:
            self.commands.put(line.strip())

    def next(self, reminder, every=30.):
        self.mic.discard_pending()
        self.endpoint.reset()
        self.vad.reset()
        fresh = remind = time.monotonic()
        while True:
            try:
                return None, self.commands.get_nowait()
            except Empty:
                pass
            if time.monotonic()-remind > every:
                print("     still listening for: " + reminder)
                remind = time.monotonic()
            frame = self.mic.read()
            if frame is None:
                continue
            if not self.endpoint.frames and time.monotonic()-fresh >= 4.:
                self.vad.reset()   # As Rex does every 4 s of quiet: Silero's state drifts numb.
                fresh = time.monotonic()
            capture = self.endpoint.push(frame, self.vad.is_speech(frame.pcm))
            if capture:
                return capture, None


def live(args, paths):
    settings = Settings.load(paths.root/"settings.toml")
    from r3x.adapters.asr import QwenASR
    from r3x.adapters.audio import Microphone, SileroVAD
    from r3x.hardware_ownership import ControllerHandoff, HardwareLocks
    handoff = ControllerHandoff(HardwareLocks())   # The microphone only: no serial port is opened.
    try:
        handoff.acquire(Cancellation())
    except ProviderFailure as exc:
        print(f"Could not take the microphone ({exc}). If Rex is running, tell him to shut down first.")
        return 1
    folder = paths.root/"validation"/("voice-encoders-" + uuid4().hex[:8])
    (folder/"clips").mkdir(parents=True, mode=0o700)
    log, machine = Log(folder/"log.jsonl"), Machine()
    sampler = Sampler(machine, log)
    providers = []
    try:
        print("Loading (about 20 seconds)...")
        sampler.start()
        vad = SileroVAD(settings.vad_threshold)
        asr = QwenASR(paths.root/"models/qwen3-asr", settings.asr_trust_logprob, settings.transcript_corrections)
        for provider in (vad, asr):
            provider.start()
            providers.append(provider)
        encoders, load = start_encoders(paths, machine, log)
        providers.extend(encoders.values())
        camera = None
        if settings.camera_enabled and not args.no_camera:
            from r3x.adapters.camera import Camera
            camera = Camera(settings.camera_device_name, paths.root/"models/faces", width=settings.camera_width,
                            height=settings.camera_height, framerate=settings.camera_framerate)
            try:
                camera.start()
                providers.append(camera)
            except Exception as exc:   # Optional background load only.
                print(f"Camera unavailable ({exc or type(exc).__name__}); continuing without its background load.")
                camera.close()
                camera = None
        mic = Microphone(settings, lambda: False)
        mic.start()
        providers.append(mic)
        log.emit("started", name=args.name, camera_load=camera is not None, device=mic.device_name,
                 end_silence_seconds=settings.end_silence_seconds, cores=machine.cores, baseline_rss_mb=machine.rss_mb())
        sampler.activity = "listening"
        listener, clips = Listener(mic, vad, settings), []

        def say(phase, speaker, lines, minimum=0.):
            for k, line in enumerate(lines, 1):
                for attempt in range(3):
                    print(f"  [{k}/{len(lines)}]  \"{line}\"" if line else f"  [{k}/{len(lines)}]  (anything you like)")
                    capture, command = listener.next(f"\"{line}\"" if line else "anything you like")
                    if command is not None:
                        if command.lower() == "q":
                            raise KeyboardInterrupt
                        print("     skipped")
                        break
                    sampler.activity = "analyzing"
                    results = analyze_both(encoders, capture, len(clips)+1, machine, sampler)
                    sampler.activity = "asr"
                    began = time.perf_counter()
                    transcript = asr.transcribe(capture, Cancellation())
                    asr_seconds = time.perf_counter()-began
                    sampler.activity = "listening"
                    voiced = sum(b-a for a, b in capture.voiced_intervals)
                    usable = transcript.trusted and all(r[0].embedding for r in results.values())
                    if minimum and (voiced < minimum or not usable) and attempt < 2:
                        print(f"     heard only \"{transcript.text}\" ({voiced:.1f} s of speech). A pause of half a second ends a line,"
                              " as it does for Rex: please read the whole sentence again in one go.")
                        continue
                    n = len(clips)+1
                    write_wav(folder/"clips"/f"{n:03d}.wav", capture.pcm)
                    clip = {"n": n, "file": f"clips/{n:03d}.wav", "phase": phase, "speaker": speaker, "prompt": line,
                            "text": transcript.text, "trusted": transcript.trusted, "seconds": len(capture.pcm)/32000,
                            "voiced_seconds": voiced, "voiced_intervals": capture.voiced_intervals,
                            "truncated": capture.truncated, "asr_seconds": asr_seconds}
                    log.emit("clip", **clip, encoders={name: {**cost, "windows": len(obs.windows), "reason": obs.reason,
                             "consistency": obs.consistency, "embedded": bool(obs.embedding)} for name, (obs, cost) in results.items()})
                    clips.append({**clip, "results": results})
                    timing = "   ".join(f"{name} {cost['seconds']:.3f} s" for name, (_, cost) in results.items())
                    print(f"     heard \"{transcript.text}\" ({voiced:.1f} s of speech)   {timing}")
                    break

        name = args.name
        print(f"\nVoice encoder test: CAM++ (Rex's voice ID today) against ERes2NetV2. Nothing on Rex will move.")
        print("Stand or sit where you usually talk to Rex. Wait for each line to appear, then say it in your normal voice.")
        print("Press Enter alone to skip a line; type q and Enter to finish early.\n")
        try:
            print(f"Part 1 of 4: {name}'s voice print. Read each sentence.")
            say("enroll", name, [s.format(name=name) for s in ENROLL], ENROLL_VOICED)
            print("\nPart 2 of 4: short lines, the kind Rex gets wrong most.")
            say("short", name, SHORT)
            print("\nPart 3 of 4: ordinary sentences.")
            say("normal", name, NORMAL)
            print(f"\n  Now {OWN_WORDS} things in your own words, as you would say them to Rex.")
            say("own_words", name, [""]*OWN_WORDS)
            print("\nPart 4 of 4 (optional): someone else. If another person is here, type their name and press Enter.")
            print("Otherwise just press Enter to finish.")
            guest = None
            while guest is None:
                capture, guest = listener.next("a name typed in the terminal, or Enter")
            if guest and guest.lower() != "q":
                print(f"\n  {guest}'s voice print. Read each sentence.")
                say("enroll", guest, [s.format(name=guest) for s in ENROLL[:3]], ENROLL_VOICED)
                print(f"\n  {guest}: short lines.")
                say("short", guest, SHORT)
                print(f"\n  {guest}: ordinary sentences.")
                say("normal", guest, NORMAL[:4])
        except KeyboardInterrupt:
            print("\nFinishing early.")
        sampler.activity = "summary"
        mic.close()
        if camera:
            camera.close()
        print("\nComparing against 25 famous voices for the results (about half a minute)...")
        rivals = famous_prints(Path(__file__).resolve().parents[1], encoders, vad)
        summary = summarize(clips, encoders, rivals, load, sampler.samples)
        summary.update({"source": "live microphone on this Mac", "camera_load": camera is not None, "lines": len(clips),
                        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20})
        (folder/"summary.json").write_text(json.dumps(summary, indent=2))
        report(summary, folder)
        return 0
    finally:
        sampler.stop.set()
        for provider in reversed(providers):
            try:
                provider.close()
            except Exception:
                pass
        handoff.close()


def saved(args, paths):
    from r3x.adapters.audio import SileroVAD
    folder = args.folder.resolve()
    rows = [json.loads(line) for line in (folder/"log.jsonl").read_text().splitlines()]
    entries = [r for r in rows if r["event"] == "clip"]
    output = folder/("rescored-" + uuid4().hex[:8])
    output.mkdir(mode=0o700)
    log, machine = Log(output/"log.jsonl"), Machine()
    vad = SileroVAD(.5)
    vad.start()
    encoders, load = start_encoders(paths, machine, log)
    clips = []
    for entry in entries:
        pcm = read_wav(folder/entry["file"])
        intervals = entry.get("voiced_intervals")
        capture = vad_capture(pcm, vad, "saved")
        if intervals:
            capture = Capture(capture.capture_id, "saved", pcm, 0., intervals[-1][1], len(pcm)/32000,
                              voiced_intervals=tuple(tuple(i) for i in intervals))
        voiced = sum(b-a for a, b in capture.voiced_intervals)
        results = analyze_both(encoders, capture, entry["n"], machine)
        log.emit("clip", n=entry["n"], voiced_seconds=voiced, encoders={name: {**cost, "windows": len(obs.windows)}
                 for name, (obs, cost) in results.items()})
        clips.append({**entry, "voiced_seconds": voiced, "truncated": entry.get("truncated", False), "results": results})
    rivals = famous_prints(Path(__file__).resolve().parents[1], encoders, vad)
    summary = summarize(clips, encoders, rivals, load, [])
    summary.update({"source": "saved lines re-scored offline (timing without the live microphone or camera)",
                    "from": str(folder), "lines": len(clips)})
    (output/"summary.json").write_text(json.dumps(summary, indent=2))
    report(summary, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    run = commands.add_parser("live")
    run.add_argument("--name", default="Bret", help="the first speaker's name (default Bret)")
    run.add_argument("--no-camera", action="store_true", help="skip the camera's background face detection")
    again = commands.add_parser("saved")
    again.add_argument("folder", type=Path)
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    if args.command == "prepare":
        return prepare(paths)
    if not (paths.root/"models/eres2netv2.onnx").is_file():
        print("The ERes2NetV2 model is missing: run  .venv/bin/python tools/check_voice_encoders.py prepare")
        return 1
    return live(args, paths) if args.command == "live" else saved(args, paths)


if __name__ == "__main__":
    sys.exit(main())
