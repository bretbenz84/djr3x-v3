"""Stage 0 telepresence measurement: the camera with no face models, and what its video costs.

On the robot, with Rex off:
    .venv/bin/python tools/measure_telepresence_camera.py [--seconds 30] [--width 960] [--quality 70]

Runs the camera as telepresence does (capture only, ffmpeg scaling the configured mode down to
--width), encodes the newest frame as JPEG VIDEO_FPS times a second like the call's video sender,
and reports frames delivered, encode time, JPEG size and the upload it implies, and the CPU and
memory of this process and of ffmpeg, sampled each second. Nothing is sent or saved but the report
(local/telepresence/). Uses camera_device_name and camera_width/height/framerate from settings.
"""
from pathlib import Path
import argparse
import json
import statistics
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.telepresence.call import VIDEO_FPS, VIDEO_QUALITY, VIDEO_WIDTH, Resources  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--seconds', type=float, default=30.)
    parser.add_argument('--width', type=int, default=VIDEO_WIDTH)
    parser.add_argument('--quality', type=int, default=VIDEO_QUALITY)
    parser.add_argument('--fps', type=float, default=VIDEO_FPS)
    args = parser.parse_args()
    from r3x.adapters.camera import Camera
    from r3x.hardware_ownership import HardwareLocks
    from r3x.sentry_recording import jpeg
    from r3x.settings import ScratchPaths, Settings
    paths = ScratchPaths.for_project(PROJECT)
    settings_path = paths.root / 'settings.toml'
    settings = Settings.load(settings_path) if settings_path.exists() else Settings()
    if not settings.camera_device_name:
        raise SystemExit('Set camera_device_name in local/settings.toml')
    if HardwareLocks().controller_running():
        raise SystemExit('Rex (or telepresence) is running and owns the camera; stop it first')
    camera = Camera(settings.camera_device_name, None, width=settings.camera_width, height=settings.camera_height,
                    framerate=settings.camera_framerate, output_width=min(args.width, settings.camera_width))
    resources = Resources()
    began = time.monotonic()
    camera.start()
    startup = time.monotonic() - began
    print(f'camera up in {startup:.1f} s at {camera.frame_width}x{camera.frame_height}; measuring {args.seconds:g} s...', flush=True)
    resources.sample()
    encode, sizes, samples, seen, last = [], [], [], 0, None
    started = time.monotonic()
    next_frame = next_sample = started
    try:
        while time.monotonic() - started < args.seconds:
            now = time.monotonic()
            if now >= next_frame:
                next_frame += 1 / args.fps
                frame = camera.hub.peek()
                if frame is not None and frame.frame_id != last:
                    last, seen = frame.frame_id, seen + 1
                    t = time.monotonic()
                    data = jpeg(frame, width=frame.width, quality=args.quality)
                    encode.append(time.monotonic() - t)
                    sizes.append(len(data))
            if now >= next_sample:
                next_sample += 1.
                samples.append(resources.sample([camera.process.pid]))
            time.sleep(max(0., min(next_frame, next_sample) - time.monotonic()))
    finally:
        camera.close()
    seconds = time.monotonic() - started
    usable = [s for s in samples if s.get('cpu_percent') is not None]
    mean = lambda key: round(statistics.mean(s[key] for s in usable if key in s), 1) if any(key in s for s in usable) else None
    report = {'evidence': 'live camera on this Mac', 'camera': settings.camera_device_name,
              'capture_mode': f'{settings.camera_width}x{settings.camera_height}@{settings.camera_framerate:g}',
              'sent_size': f'{camera.frame_width}x{camera.frame_height}', 'startup_seconds': round(startup, 2),
              'seconds': round(seconds, 1), 'target_fps': args.fps, 'frames_encoded': seen,
              'fps': round(seen / seconds, 1) if seconds else None, 'quality': args.quality,
              'encode_ms_median': round(1000 * statistics.median(encode), 1) if encode else None,
              'encode_ms_max': round(1000 * max(encode), 1) if encode else None,
              'jpeg_kb_median': round(statistics.median(sizes) / 1024, 1) if sizes else None,
              'video_upload_mbit_s': round(sum(sizes) * 8 / seconds / 1e6, 2) if sizes else None,
              'python_cpu_percent_mean': mean('cpu_percent'), 'ffmpeg_cpu_percent_mean': mean('ffmpeg_cpu_percent'),
              'ffmpeg_rss_mb': mean('ffmpeg_rss_mb'), 'python_peak_rss_mb': samples[-1]['peak_rss_mb'] if samples else None,
              'note': 'CPU is percent of one core; the M2 has eight.'}
    out = paths.root / 'telepresence'
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out / time.strftime('camera-%Y%m%d-%H%M%S.json')
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f'saved {path.relative_to(PROJECT)}')


if __name__ == '__main__':
    main()
