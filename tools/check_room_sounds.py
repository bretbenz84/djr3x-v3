"""What Rex's room listener logged (doorbells, knocks, dogs, alarms), for counting false alarms.

  report [--since DATE] [--labels FILE]   totals from the session logs: time listened, events by kind and
                                          score, and how many came while Rex made sound or moved. With
                                          --labels (JSON, event id -> "real" or what it actually was),
                                          real and false alarms at each score.
  listen OUT [--since DATE]               copy each logged clip into a new folder OUT, numbered, with an
                                          index and an empty labels.json, for the owner to listen to.

DATE is YYYY-MM-DD (sessions written since then). Reads local/sessions and local/room-sounds; nothing is
played, recorded or sent. docs/ROOM_SOUNDS.md.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys

from r3x.room_sounds import GROUPS, LOG_SCORE
from r3x.settings import ScratchPaths


BANDS = (LOG_SCORE, .4, .5, .6, .7, .8)


def logged(paths, since):
    """(events, seconds listened) from the session logs, oldest first."""
    events, heard = [], 0
    for log in sorted((paths.root / 'sessions').glob('*.jsonl'), key=lambda p: p.stat().st_mtime):
        if since and datetime.fromtimestamp(log.stat().st_mtime) < since:
            continue
        last = None
        with log.open(encoding='utf-8') as handle:
            for line in handle:
                if '"room_sound' not in line:
                    continue
                row = json.loads(line)
                if row['event'] == 'room_sound':
                    events.append(row)
                elif row['event'] == 'room_sounds_listened':
                    last = row   # Running totals: the last one covers the run.
        heard += last['heard_seconds'] if last else 0
    return events, heard


def report(args, paths):
    events, heard = logged(paths, args.since)
    labels = json.loads(args.labels.read_text()) if args.labels else {}
    by_group = {}
    for group in GROUPS:
        mine = [e for e in events if e['group'] == group]
        entry = {'events': len(mine),
                 'at_or_above': {f'{band:g}': sum(e['peak_score'] >= band for e in mine) for band in BANDS},
                 'while_rex_made_sound': sum(e['rex_sound'] for e in mine),
                 'while_servos_moved': sum(e['servos'] for e in mine),
                 'while_wheels_moved': sum(e['wheels'] for e in mine),
                 'on_talk': sum(e['voice_score'] >= .5 for e in mine)}
        if labels:
            judged = [e for e in mine if labels.get(e['event_id'])]
            entry['labelled'] = len(judged)
            entry['real_and_false_at_or_above'] = {f'{band:g}': [
                sum(e['peak_score'] >= band and labels[e['event_id']] == 'real' for e in judged),
                sum(e['peak_score'] >= band and labels[e['event_id']] != 'real' for e in judged)] for band in BANDS}
            entry['false_alarms_were'] = dict(Counter(labels[e['event_id']] for e in judged
                                                      if labels[e['event_id']] != 'real'))
        by_group[group] = entry
    hours = heard / 3600
    print(json.dumps({'evidence': 'live_trace', 'hours_listened': round(hours, 2), 'events': len(events),
                      'per_hour': round(len(events) / hours, 2) if hours else None,
                      'clips_missing': sum(e.get('clip') is None for e in events), 'by_kind': by_group}, indent=1))


def listen(args, paths):
    events, _ = logged(paths, args.since)
    events = [e for e in events if e.get('clip')]
    if not events:
        print('No logged clips yet.')
        return 0
    args.out.mkdir(parents=True, mode=0o700)   # A new folder: nothing is overwritten.
    lines, labels = [], {}
    for number, event in enumerate(events, 1):
        source = paths.root / 'room-sounds' / event['session_id'] / event['clip']
        when = event['local_time'].replace('T', ' ')[:16]
        marks = ', '.join(m for m, on in (('Rex making sound', event['rex_sound']), ('servos moving', event['servos']),
                                           ('wheels moving', event['wheels'])) if on)
        name = f"R{number:03d} {event['group']} {event['peak_score']:.2f} - {when.replace(':', '.')}.wav"
        if not source.is_file():
            lines.append(f'R{number:03d}  (clip missing: {event["clip"]})')
            continue
        shutil.copy2(source, args.out / name)
        lines.append(f"R{number:03d}  {when}  {event['group']:8} {event['peak_score']:.2f} {event['peak_class']:28}"
                     f" voice {event['voice_score']:.2f}  {event['seconds']:4.1f} s  {marks}  [{event['event_id']}]")
        labels[event['event_id']] = ''
    (args.out / '00 index.txt').write_text(
        'Sounds Rex logged as a doorbell, knock, dog or alarm. Listen, then say what each really was.\n'
        'number, time, kind, score, the class that scored, voice score, length, what Rex was doing [event id]\n\n'
        + '\n'.join(lines) + '\n')
    (args.out / 'labels.json').write_text(json.dumps(labels, indent=1) + '\n')
    print(f'{len(labels)} clips in {args.out}')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    totals = commands.add_parser('report')
    totals.add_argument('--labels', type=Path, help='JSON object: event id -> "real" or what it actually was')
    clips = commands.add_parser('listen')
    clips.add_argument('out', type=Path)
    for command in (totals, clips):
        command.add_argument('--since', type=datetime.fromisoformat)
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    return report(args, paths) if args.command == 'report' else listen(args, paths)


if __name__ == '__main__':
    sys.exit(main())
