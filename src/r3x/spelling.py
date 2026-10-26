"""The owner's spelling fixes for names the recognizer writes wrong (owner, October 26).

Qwen3-ASR wrote the owner's friend as "Exodia" (she is saved as Exudica, and "Exodia" was once filed as a
second person) and the owner as "Brett" (he spells it Bret). Each entry of `transcript_corrections` in
settings replaces that whole word, in any case, in every transcript before Rex reads it; the trace keeps
what was heard. It fixes spelling only: it never decides who spoke, who is meant or what was said about
them, and words not in the owner's list are left exactly as heard.
"""
import re

MAX_ENTRIES = 32


def compile_table(table):
    """Validated (pattern, replacement) pairs, longest heard spelling first."""
    if not isinstance(table, dict) or len(table) > MAX_ENTRIES:
        raise ValueError('InvalidTranscriptCorrections')
    pairs, seen = [], set()
    for heard, written in table.items():
        for value in (heard, written):
            if not isinstance(value, str) or not 0 < len(value.strip()) <= 40 or not re.fullmatch(r"[\w'’.\- ]+", value):
                raise ValueError('InvalidTranscriptCorrection')
        key = ' '.join(heard.split()).casefold()
        if key in seen:
            raise ValueError('DuplicateTranscriptCorrection')
        seen.add(key)
        words = r'\s+'.join(re.escape(w) for w in heard.split())
        pairs.append((len(key), re.compile(r"(?<![\w'’])" + words + r"(?!\w)", re.IGNORECASE), ' '.join(written.split())))
    return tuple((pattern, written) for _, pattern, written in sorted(pairs, key=lambda p: -p[0]))


def correct(text, pairs):
    """The text with the owner's spellings; unchanged when nothing in it is listed."""
    for pattern, written in pairs:
        text = pattern.sub(written, text)
    return text
