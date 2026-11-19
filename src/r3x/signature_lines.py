"""Rex's Star Tours lines, rationed by code (owner, November 19).

On the ride, Captain Rex says "I have a very bad feeling about this!" as the speeder heads into a comet, and
"Uh... I meant to do that! A little shortcut... Ha ha!" after taking the wrong turn into the maintenance bay.
The owner wants both back ("currently it's never done") without them being overdone ("I'm weary about
prompting the LLM"). So code decides when a line is ready: each rests REST_SECONDS after Rex was heard saying
it, across restarts, and only a ready line is mentioned to the model at all. The conversation owner still
chooses whether the moment fits, in the same single generation: no extra model call, nothing spoken by code.
A line counts as said only when it was heard (matched in Rex's own delivered words, never a person's).
"""

SCHEMA = """CREATE TABLE IF NOT EXISTS signature_lines(
    line TEXT PRIMARY KEY, said_at REAL NOT NULL);"""

REST_SECONDS = 2 * 3600.   # About once a visit: rare enough to stay a treat.

# Each line and the words that mark it in his own heard text.
LINES = {
    'bad_feeling': ('bad feeling',),
    'meant_to': ('meant to do that',),
}


def ready(store, now) -> tuple[str, ...]:
    """The lines that have rested long enough to be offered now (wall clock `now`)."""
    said = {r['line']: r['said_at'] for r in store.rows('SELECT line, said_at FROM signature_lines')}
    return tuple(name for name in LINES if now - said.get(name, float('-inf')) >= REST_SECONDS)


def heard(conn, text, now) -> tuple[str, ...]:
    """Record each line found in a heard line of Rex's own; returns the lines found."""
    lowered = ' '.join(str(text or '').lower().replace('’', "'").split())
    found = tuple(name for name, marks in LINES.items() if any(m in lowered for m in marks))
    for name in found:
        conn.execute('INSERT OR REPLACE INTO signature_lines VALUES(?,?)', (name, now))
    return found
