"""What Rex's room map holds, and what a run's log says it did (read only).

    .venv/bin/python tools/room_map_report.py                 the map, and the latest session log
    .venv/bin/python tools/room_map_report.py SESSION_ID      the map, and that session's log

November 22 (docs/ROOM_NAVIGATION_PLAN.md): buildings, spots with their photos by direction, which rooms
people named and which way rooms lie, the passages between spots, and from the log the spots made and left,
photos filed, passages recorded, rooms gone into, doubtful sightings and route corrections. Opens the store
read-only; writes nothing.
"""
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv):
    db = sqlite3.connect((ROOT / 'local/core.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'buildings' not in tables:
        print('No room map yet (Rex has not started with it).')
    else:
        rooms = {r['id']: r['name'] for r in db.execute('SELECT id,name FROM places')}
        for b in db.execute('SELECT * FROM buildings ORDER BY created_at'):
            networks = db.execute('SELECT COUNT(*) FROM building_networks WHERE building_id=?', (b['id'],)).fetchone()[0]
            print(f"\n{b['name'] or '(unnamed)'}{' (provisional)' if b['provisional'] else ''}: {networks} router(s)")
            print('  rooms:', ', '.join(sorted(r['name'] for r in db.execute('SELECT name FROM places WHERE building_id=?', (b['id'],)))) or '-')
            loose = db.execute("SELECT COUNT(*) FROM room_photos WHERE building_id=? AND spot_id IS NULL", (b['id'],)).fetchone()[0]
            print(f'  loose October views: {loose}')
            for s in db.execute('SELECT * FROM spots WHERE building_id=? ORDER BY created_at', (b['id'],)):
                photos = db.execute('SELECT bearing,description FROM room_photos WHERE spot_id=?', (s['id'],)).fetchall()
                ways = db.execute('SELECT room_id,bearing,source FROM room_bearings WHERE spot_id=?', (s['id'],)).fetchall()
                room = rooms.get(s['room_id']) or '-'
                print(f"  spot {s['id'][:8]}: room {room} ({s['room_source'] or 'not named'}), {len(photos)} photos at "
                      + (', '.join(str(round(p['bearing'])) for p in photos if p['bearing'] is not None) or '-'))
                for w in ways:
                    print(f"     {rooms.get(w['room_id'], '?')} lies at {round(w['bearing'])} ({w['source']})")
                for p in photos:
                    if p['description']:
                        print(f"     sees: {p['description']}")
            for p in db.execute('SELECT * FROM passages WHERE building_id=? ORDER BY created_at', (b['id'],)):
                print(f"  passage {p['from_spot'][:8]} -> {p['to_spot'][:8]}: {p['length_m']} m in "
                      f"{len(json.loads(p['segments']))} segments, {p['doorways']} doorway(s), used {p['uses']}, failed {p['failures']}")
    sessions = sorted((ROOT / 'local/sessions').glob('*.jsonl'), key=lambda f: f.stat().st_mtime)
    if argv:
        sessions = [f for f in sessions if f.stem.startswith(argv[0])]
    if not sessions:
        return 0
    log = sessions[-1]
    counts, notes = Counter(), []
    for line in log.open():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get('event')
        if kind in {'place_spot', 'place_photo', 'place_passage', 'place_named', 'place_building', 'place_network',
                    'place_new', 'drive_room_entered', 'drive_weak_sighting', 'drive_route_fix', 'place_described'}:
            counts[kind + (':' + e['status'] if kind == 'place_spot' else '')] += 1
            if kind in {'place_passage', 'place_named', 'place_building', 'place_new', 'drive_room_entered'}:
                notes.append({k: v for k, v in e.items() if k not in ('session_id', 'event', 'at')} | {'event': kind})
        if kind == 'drive_receipt':
            for step in e.get('steps') or ():
                if step.get('action') in {'go_to', 'look_around'}:
                    counts[f"{step['action']}:{step.get('status')}:{step.get('reason')}"] += 1
    print(f'\nSession {log.stem[:8]}:')
    for key, n in sorted(counts.items()):
        print(f'  {key}: {n}')
    for note in notes[-20:]:
        print('  ', json.dumps(note)[:200])
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
