"""Speak a reply tool's words while the rest of the call is still being written.

October 14 improvement plan, step 5. In the October 5-13 logs 170 of 205 replies to people came
inside a tool call (respond_with_intent, respond_with_gesture), and a tool call is only handed
over when the whole response is finished, so its words started playing about 0.7-1.1 s later
than the same words streamed as plain prose. Strict tool arguments arrive as JSON in schema
order, and both reply tools put their spoken `text` first, so its characters can be decoded as
they arrive and phrased exactly like prose. Only human-turn replies use this; the finished call
still carries the same text for validation and history.
"""
import json
import re

# Tool -> the spoken field that comes first in its arguments. Reply tools stream their words; tools
# that need a look or a lookup stream their short lead-in (October 14, e1f8b2c6); moves stream their
# acknowledgment (October 16: 18 of 36 replies on October 13 were moves, median 4.3 s).
STREAMED_FIELDS = {'respond_with_intent': 'text', 'respond_with_gesture': 'text', 'inspect_view': 'say_first',
                   'inspect_visual_memory': 'say_first', 'recall_conversations': 'say_first',
                   'personal_history': 'say_first', 'drive_base': 'say_before', 'set_robot_pose': 'say_before'}
STREAMED_TOOLS = frozenset(STREAMED_FIELDS)
_ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}


class TextField:
    """Incrementally decodes the leading string field of a JSON object being streamed."""

    def __init__(self, field='text'):
        self.field = field
        self.opening = re.compile(r'\s*\{\s*"' + re.escape(field) + r'"\s*:\s*"')
        self.buffer = ''
        self.state = 'seek'     # seek -> text -> done, or off when text is not the first field
        self.at = 0

    def feed(self, chunk):
        """New decoded characters of the text, '' when none are ready."""
        if self.state in {'done', 'off'}:
            return ''
        self.buffer += chunk
        if self.state == 'seek':
            match = self.opening.match(self.buffer)
            if not match:
                stripped = self.buffer.lstrip()
                expected = '{"' + self.field + '":"'
                if (len(stripped) > len(expected) + 3 or
                        (stripped and not expected.startswith(stripped.replace(' ', '')[:len(expected)]))):
                    self.state = 'off'
                return ''
            self.state, self.at = 'text', match.end()
        out = []
        while self.at < len(self.buffer):
            char = self.buffer[self.at]
            if char == '"':
                self.state = 'done'
                break
            if char != '\\':
                out.append(char)
                self.at += 1
                continue
            if self.at + 1 >= len(self.buffer):
                break                                     # The escape's second half has not arrived.
            code = self.buffer[self.at + 1]
            if code == 'u':
                if self.at + 6 > len(self.buffer):
                    break
                try:
                    out.append(json.loads('"' + self.buffer[self.at:self.at + 6] + '"'))
                except ValueError:
                    self.state = 'off'
                    break
                self.at += 6
                continue
            out.append(_ESCAPES.get(code, code))
            self.at += 2
        return ''.join(out)
