"""Plan baseline: the numbers each step of the October 14 improvement plan is judged by.

Reads private session traces under local/sessions (read-only) and prints, for sessions
last written on or after --since:
  reply wait          end of human speech to first audio, and the model's share of it
  planned beats       started, heard, silent moves done, talked over, let go, skipped
  presence            new face track IDs per minute in view, and re-greetings of a person
                      recognized 31-120 s earlier (proxy for forgetting someone present)
  driving             trips, legs, blocked/refused legs, durations, hosted looks
  repeats             near-repeated delivered lines (word overlap), joke-record entries
  cost                model calls per hour and estimated model cost

Trace `at` is monotonic time, so a session is dated by its file's modification time.

    .venv/bin/python tools/baseline_report.py --since 2026-10-05
    .venv/bin/python tools/baseline_report.py --since 2026-10-15 --json
"""
import argparse
import collections
import datetime
import json
import math
import re
import statistics
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
# USD per million tokens: (uncached input, cached read, cache write, output). Luna and mini from
# tools/compare_character_models.py; Terra has only list input/output rates in the repo
# (docs/LUNA_COMPARISON.md), so its cached input is priced as uncached: an upper bound.
RATES = {'gpt-6-luna': (.10, .01, .125, .50), 'gpt-5.6-terra': (2., 2., 2., 12.),
         'gpt-5.4-mini': (.75, .075, .75, 4.5)}
SIDE_CALLS = {'visual_locate': 'gpt-5.4-mini', 'visual_route': 'gpt-5.4-mini',
              'observation_refresh': 'gpt-6-luna'}


def percentile(values, q):
    v = sorted(values)
    return v[max(0, math.ceil(len(v) * q) - 1)] if v else None


def spread(values):
    v = [x for x in values if x is not None]
    if not v:
        return {'n': 0}
    return {'n': len(v), 'median': round(statistics.median(v), 2), 'p90': round(percentile(v, .9), 2),
            'max': round(max(v), 2)}


def load(path):
    rows = []
    for line in path.open(encoding='utf-8'):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def cost(model, usage):
    rates = RATES.get(model)
    if not rates:
        return 0.
    fresh = usage['input_tokens'] - usage['cached_tokens'] - usage['cache_write_tokens']
    return (fresh * rates[0] + usage['cached_tokens'] * rates[1] + usage['cache_write_tokens'] * rates[2]
            + usage['output_tokens'] * rates[3]) / 1e6


def words(text):
    return set(re.findall(r"[a-z0-9']+", (text or '').lower()))


class Totals:
    def __init__(self):
        self.lists = collections.defaultdict(list)
        self.counts = collections.Counter()
        self.tokens = collections.defaultdict(collections.Counter)
        self.hours = 0.
        self.face_minutes = 0.

    def session(self, rows):
        events = collections.defaultdict(list)
        for row in rows:
            events[row['event']].append(row)
        self.hours += max(rows[-1]['at'] - rows[0]['at'], 0) / 3600
        self.counts['sessions'] += 1
        for row in events['turn_done']:
            timings = row.get('timings') or {}
            for key in ('end_of_speech_to_first_audio', 'model_first_text', 'model_first_output',
                        'opportunity_to_first_audio'):
                if timings.get(key) is not None:
                    self.lists[key].append(timings[key])
        self.lists['asr_seconds'] += [r['asr_seconds'] for r in events['transcript'] if r.get('asr_seconds') is not None]
        self.lists['tts_first_pcm_seconds'] += [r['tts_first_pcm_seconds'] for r in events['tts_first_pcm']
                                                if r.get('tts_first_pcm_seconds') is not None]
        self.beats(events)
        self.faces(events)
        self.drive(events)
        self.repeats(events)
        self.model(events)

    def beats(self, events):
        silent = {r['turn_id'] for r in events['tool_proposed'] if r.get('name') == 'remain_silent'}
        self.counts['plans_saved'] += len(events['agency_plan_saved'])
        self.counts['beats_started'] += len(events['agency_step_started'])
        self.counts['beats_let_go'] += sum(r['turn_id'] in silent for r in events['agency_step_started'])
        for row in events['agency_step_finished']:
            if row['status'] == 'completed':
                self.counts['beats_heard' if row.get('kind') in {'contribute', 'gesture'} else 'beats_silent_done'] += 1
            if row.get('delivery_status') == 'interrupted':
                self.counts['beats_talked_over'] += 1
            if row['status'] == 'skipped':
                self.counts['beats_skipped'] += 1
            if row.get('revision_reason') in {'overtaken_by_conversation', 'previous_beat_did_not_land', 'moment_passed'}:
                self.counts['beats_dropped_stale'] += 1
        self.counts['beats_deferred_by_model'] += len(events['agency_beat_deferred'])

    def faces(self, events):
        frames = events['camera_frame']
        tracks, seen = set(), collections.defaultdict(list)
        previous = None
        for row in frames:
            if previous is not None and previous[1]:
                self.face_minutes += min(row['at'] - previous[0], 2.) / 60
            previous = (row['at'], bool(row.get('face_details')))
            for face in row.get('face_details') or ():
                tracks.add(face['track_id'])
                if face.get('identity'):
                    seen[face['identity']].append(face['at'])
        self.counts['face_tracks'] += len(tracks)
        for row in events['proactive_opportunity']:
            if row.get('kind') != 'visual_arrival':
                continue
            self.counts['arrivals'] += 1
            person = row.get('person_id')
            if person and any(row['at'] - 120 <= t < row['at'] - 31 for t in seen.get(person, ())):
                self.counts['regreeted_someone_seen_31_120s_before'] += 1

    def drive(self, events):
        for row in events['drive_receipt']:
            steps = row.get('steps') or []
            self.counts['trips'] += 1
            self.counts['trips_' + str(row.get('status'))] += 1
            self.lists['legs_per_trip'].append(len(steps))
            self.lists['blocked_legs_per_trip'].append(sum(s.get('status') in {'blocked', 'refused'} for s in steps))
            if row.get('finished_at') is not None and row.get('started_at') is not None:
                self.lists['trip_seconds'].append(row['finished_at'] - row['started_at'])
        self.counts['drive_steps'] += len(events['drive_step'])
        self.counts['drive_steps_blocked_or_refused'] += sum(r.get('status') in {'blocked', 'refused'}
                                                             for r in events['drive_step'])
        self.counts['drive_readings_rechecked'] += len(events['drive_reading_rechecked'])
        self.counts['hosted_looks'] += len(events['visual_locate']) + len(events['visual_route'])

    def repeats(self, events):
        lines = [w for w in (words(r.get('text')) for r in events['phrase_completed']) if len(w) >= 6]
        self.counts['lines_6_words_or_more'] += len(lines)
        self.counts['near_repeated_lines'] += sum(
            max((len(a & b) / len(a | b) for b in lines[:i]), default=0) >= .7 for i, a in enumerate(lines))
        self.counts['jokes_recorded'] += len(events['joke_recorded'])
        self.counts['joke_material_written'] += len(events['joke_material'])

    def model(self, events):
        self.counts['model_requests'] += len(events['model_request'])
        for row in events['model_usage']:
            usage = self.tokens[row['model']]
            for key in ('input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens'):
                usage[key] += row.get(key) or 0
        for name, model in SIDE_CALLS.items():
            for row in events[name]:
                usage, details = row.get('usage') or {}, (row.get('usage') or {}).get('input_tokens_details') or {}
                self.tokens[model]['input_tokens'] += usage.get('input_tokens', 0)
                self.tokens[model]['cached_tokens'] += details.get('cached_tokens', 0)
                self.tokens[model]['cache_write_tokens'] += details.get('cache_write_tokens', 0)
                self.tokens[model]['output_tokens'] += usage.get('output_tokens', 0)

    def report(self):
        usd = sum(cost(model, usage) for model, usage in self.tokens.items())
        waits = self.lists['end_of_speech_to_first_audio']
        hours = self.hours or 1e-9
        return {
            'sessions': self.counts['sessions'], 'hours': round(self.hours, 2),
            'reply_wait': {**spread(waits), 'over_4s': sum(w > 4 for w in waits)},
            'model_first_output': spread(self.lists['model_first_output']),
            'model_first_text': spread(self.lists['model_first_text']),
            'asr_seconds': spread(self.lists['asr_seconds']),
            'tts_first_pcm_seconds': spread(self.lists['tts_first_pcm_seconds']),
            'beats': {k: self.counts[k] for k in ('plans_saved', 'beats_started', 'beats_heard', 'beats_silent_done',
                      'beats_talked_over', 'beats_let_go', 'beats_skipped', 'beats_dropped_stale',
                      'beats_deferred_by_model')},
            'presence': {'face_minutes': round(self.face_minutes, 1),
                         'new_face_tracks_per_minute': round(self.counts['face_tracks'] / self.face_minutes, 1)
                         if self.face_minutes else None,
                         'arrivals_greeted': self.counts['arrivals'],
                         'regreeted_someone_seen_31_120s_before': self.counts['regreeted_someone_seen_31_120s_before']},
            'driving': {'trips': self.counts['trips'],
                        'completed': self.counts['trips_completed'], 'partial': self.counts['trips_partial'],
                        'legs_per_trip': spread(self.lists['legs_per_trip']),
                        'blocked_legs_per_trip': spread(self.lists['blocked_legs_per_trip']),
                        'trip_seconds': spread(self.lists['trip_seconds']),
                        'steps_blocked_or_refused': self.counts['drive_steps_blocked_or_refused'],
                        'readings_rechecked': self.counts['drive_readings_rechecked'],
                        'hosted_looks': self.counts['hosted_looks']},
            'humor': {'lines_6_words_or_more': self.counts['lines_6_words_or_more'],
                      'near_repeated_lines': self.counts['near_repeated_lines'],
                      'jokes_recorded': self.counts['jokes_recorded'],
                      'joke_material_written': self.counts['joke_material_written']},
            'cost': {'model_requests_per_hour': round(self.counts['model_requests'] / hours, 1),
                     'estimated_model_usd': round(usd, 2), 'usd_per_hour': round(usd / hours, 2)},
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--since', default='2026-10-05', help='first session date (file modification date)')
    parser.add_argument('--until', default=None, help='last session date, inclusive')
    parser.add_argument('--sessions', type=Path, default=PROJECT / 'local' / 'sessions')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    since = datetime.date.fromisoformat(args.since)
    until = datetime.date.fromisoformat(args.until) if args.until else datetime.date.max
    totals = Totals()
    for path in sorted(args.sessions.glob('*.jsonl')):
        day = datetime.date.fromtimestamp(path.stat().st_mtime)
        if since <= day <= until:
            rows = load(path)
            if len(rows) >= 2:
                totals.session(rows)
    result = totals.report()
    if args.json:
        print(json.dumps(result, indent=1))
        return
    for section, values in result.items():
        if isinstance(values, dict):
            print(section)
            for key, value in values.items():
                print(f'  {key:42} {value}')
        else:
            print(f'{section:44} {values}')


if __name__ == '__main__':
    main()
