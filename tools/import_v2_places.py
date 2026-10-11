"""Copy V2's taught rooms (names and MobileCLIP-S2 views) into V3's store. Owner-authorized.

    .venv/bin/python tools/import_v2_places.py            # preview only, writes nothing
    .venv/bin/python tools/import_v2_places.py --apply    # copy (Rex must not be running)

Reads a consistent read-only SQLite snapshot of V2's data/places.db (V2 is never written).
Only vectors tagged mobileclip_s2_v1 are copied, and only because V3's exported encoder
reproduces V2's embeddings (docs/ROOM_RECOGNITION.md). Rooms get new V3 IDs with
`import_ref` naming the snapshot digest and V2 place_id; the mapping is kept in `imports`.
V2's "don't drive here" flags are not imported (not part of this feature).
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

SOURCE = Path('/Users/bbenziger/djr3x-v2/data/places.db')
TAG = 'mobileclip_s2_v1'


def snapshot(path):
    copy = sqlite3.connect(':memory:'); copy.row_factory = sqlite3.Row
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as source:
        source.backup(copy)   # Includes committed WAL pages; no partial main-file copy.
    return copy, hashlib.sha256(copy.serialize()).hexdigest()


def read(copy):
    import numpy as np
    rooms = []
    for place in copy.execute('SELECT place_id,name,created_at FROM places ORDER BY place_id'):
        views = []
        for row in copy.execute('SELECT embedding_id,vector,dim,heading_deg,captured_at FROM place_embeddings '
                                'WHERE place_id=? AND model_tag=? ORDER BY captured_at', (place['place_id'], TAG)):
            vector = np.frombuffer(bytes(row['vector']), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if row['dim'] != 512 or vector.shape != (512,) or not 0.99 < norm < 1.01:
                raise SystemExit(f'Unexpected V2 vector {row["embedding_id"]} in {place["name"]!r}')
            views.append({'vector': vector, 'heading': row['heading_deg'],
                          'captured_at': datetime.fromisoformat(row['captured_at']).timestamp()})
        rooms.append({'source_id': place['place_id'], 'name': ' '.join(place['name'].split()), 'views': views})
    return rooms


def look_alikes(rooms):
    """Pairs of rooms whose views resemble each other (V2 found contaminated galleries this way)."""
    import numpy as np
    pairs = []
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            if a['views'] and b['views']:
                sims = np.stack([v['vector'] for v in a['views']]) @ np.stack([v['vector'] for v in b['views']]).T
                pairs.append((a['name'], b['name'], round(float(sims.max()), 3), round(float(sims.mean()), 3)))
    return sorted(pairs, key=lambda p: p[2], reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--source', type=Path, default=SOURCE)
    args = parser.parse_args()
    copy, digest = snapshot(args.source)
    try:
        rooms = read(copy)
    finally:
        copy.close()
    print(f'V2 snapshot sha256 {digest}')
    for room in rooms:
        print(f'  {room["name"]!r}: {len(room["views"])} views')
    for a, b, best, mean in look_alikes(rooms)[:6]:
        print(f'  look-alike check {a!r} vs {b!r}: max {best}, mean {mean}')
    rooms = [r for r in rooms if r['views']]
    if not args.apply:
        print('Preview only; nothing written. Re-run with --apply to copy.')
        return
    project = Path(__file__).resolve().parents[1]
    from r3x.lease import LiveLease
    from r3x.memory import Memory, label
    from r3x.places import MAX_EMBEDDINGS
    lease = LiveLease(project / 'local/sessions/live.lock'); lease.acquire()   # Refuses while Rex runs.
    memory = Memory(project / 'local/core.sqlite3'); memory.start()
    try:
        with memory.transaction() as conn:
            key = 'v2-places:' + digest
            old = conn.execute('SELECT mapping FROM imports WHERE digest=?', (key,)).fetchone()
            if old:
                print('Already imported:', old[0]); return
            existing = {label(r[0]) for r in conn.execute('SELECT name FROM places')}
            clash = [r['name'] for r in rooms if label(r['name']) in existing]
            if clash:
                raise SystemExit(f'V3 already has rooms named {clash}; nothing written')
            mapping = {}
            for room in rooms:
                place_id = uuid4().hex
                mapping[str(room['source_id'])] = place_id
                conn.execute('INSERT INTO places VALUES(?,?,?,?,?)', (place_id, room['name'],
                             min(v['captured_at'] for v in room['views']), None, f'{key}:{room["source_id"]}'))
                for view in room['views'][-MAX_EMBEDDINGS:]:
                    conn.execute('INSERT INTO place_embeddings VALUES(?,?,?,?,?,?,?)', (uuid4().hex, place_id, TAG,
                                 view['vector'].astype('float32').tobytes(), view['heading'], view['captured_at'], 'v2-import'))
            conn.execute('INSERT INTO imports VALUES(?,?)', (key, json.dumps(mapping)))
        print(f'Imported {len(rooms)} rooms:', json.dumps(mapping))
    finally:
        memory.close(); lease.close()


if __name__ == '__main__':
    main()
