"""Rex's picture of the present: who is here, what animals and things are around, the
conversation clock, and what Rex himself is doing. One bookkeeper, no model, no device.

October 14 improvement plan, step 1. Before this, each feature kept its own short memory
with its own time limit: the goal's 45/180-second presence, the memory gate's 90-second
visit carry and 10-minute company rule, a 60-second "spoke recently" arrival check, the
pet arrival gaps. Faces get a new track ID about 32 times a minute (October 5-13 logs) and
6 of 8 arrival greetings went to someone recognized under two minutes earlier. Everything
that asks "is this person still here", "whose private memory may be used" or "is the dog
still around" now asks this one owner, which answers with the rules below.

The model cannot feel time: "40 seconds ago" is a fact to it, not a feeling. describe()
turns the clock into what it means ("just now", "still waiting for an answer", "that moment
has passed") so every reply, beat and plan sees the moment the way a person would.

Recognition here is evidence of presence only. Nothing in this module names a speaker,
enrolls a voice or face, or interprets what anyone said; alone_with() is one input to the
state owner's speaker deduction (state.CoreState.infer_speaker).
"""
from collections import deque
from datetime import datetime
import threading
from uuid import uuid4

# A person recognized (face or supported voice) this recently is here.
HERE_SECONDS = 45.
# An unrecognized face or voice continues a known person's visit this long after their last
# recognition, while nobody else is recognized. Live sessions showed recognition gaps of up to
# 50 seconds while the owner stayed put; beyond this, an unrecognized person is someone else.
STAYS_SECONDS = 180.
# A known person whose current visit began this recently has just arrived. A visit only
# breaks after STAYS_SECONDS without recognition, so looking away, a renumbered face track or
# Rex's own turning is not leaving and coming back.
ARRIVAL_SECONDS = 60.
# With nobody seen or heard this long, nobody is around: no new goals, no pet remarks.
ALONE_SECONDS = 300.
# Private memory: how long a recognized person stays the private audience while the camera
# cannot confirm them (October 3: his own movement restarted the owner's face track a second
# before a recall). Stricter than presence: memory must not reach the wrong person.
PRIVATE_CARRY_SECONDS = 90.
# After two faces are seen together, only direct recognition opens private memory for this
# long (October 3: a guest briefly alone in view was answered as the owner).
COMPANY_SECONDS = 600.
# A detector drops and relabels animals (dog, cat, nothing) from one frame to the next.
PET_LABELS = frozenset({'dog', 'cat'})
PET_RECALL_SECONDS = 10.    # A pet seen this recently is still targetable between detections.
PET_GAP_SECONDS = 4.        # Dropouts shorter than this are the same sighting.
PET_SETTLE_SECONDS = 2.5    # In view this long before its arrival counts.
PET_ABSENT_SECONDS = 120.   # Gone this long, a reappearance is a new arrival.
PET_COOLDOWN_SECONDS = 300. # At most one pet arrival this often.
THINGS_KEPT = 8             # Recently seen object labels kept for the picture.
# October 14 live run (e1f8b2c6): with only his last line in the picture, Rex owned one clock
# mistake four times in five minutes. His last few heard lines are kept as "already said".
RECENT_LINES = 6
# Someone recognized in view (or by voice) when Rex spoke was spoken with: the same run showed
# a greeting prepared for the owner 74 s in, after they had already talked.
ENGAGED_SECONDS = 10.


def ago(seconds):
    """How long ago, the way a person would say it."""
    if seconds is None:
        return None
    if seconds < 3: return 'just now'
    if seconds < 15: return 'a few seconds ago'
    if seconds < 60: return 'under a minute ago'
    if seconds < 180: return 'a couple of minutes ago'
    if seconds < 600: return 'several minutes ago'
    if seconds < 3600: return 'a while ago'
    return 'a long time ago'


def quiet_meaning(seconds):
    """What a stretch of quiet means in conversation."""
    if seconds is None: return 'nobody has spoken yet this run'
    if seconds < 3: return 'conversation is active'
    if seconds < 10: return 'a short pause: people may still be thinking'
    if seconds < 45: return 'a lull'
    if seconds < 180: return 'a long quiet stretch: the last topic has gone cold'
    return 'nobody has talked for minutes'


def local_time(moment):
    """The wall clock the way a person keeps it in mind: 'Wednesday evening, 6:24 PM, October 14'."""
    if moment is None:
        return None
    hour = moment.hour
    part = ('night' if hour < 5 else 'early morning' if hour < 9 else 'morning' if hour < 12 else
            'afternoon' if hour < 17 else 'evening' if hour < 21 else 'night')
    clock = moment.strftime('%I:%M %p').lstrip('0')
    return f"{moment.strftime('%A')} {part}, {clock}, {moment.strftime('%B')} {moment.day}"


def line_meaning(line, answered, seconds):
    """What became of Rex's last line, in words."""
    if answered:
        return 'someone has spoken since'
    if line['question']:
        if seconds < 8: return 'his question was just asked: give them time to answer'
        if seconds < 30: return 'his question is still waiting for an answer'
        return 'his question went unanswered and that moment has passed: do not repeat it or ask again'
    if seconds < 15: return 'just said; nobody has answered yet'
    return 'nobody answered; that moment has passed'


class Pets:
    """An animal coming into view is something to react to, like a person walking in.

    Owner, October 13: Max sat in his arms for minutes and Rex never noticed; only faces
    could start an arrival. Any pet label counts and short gaps do not end the sighting."""

    def __init__(self):
        self.since = self.last_seen = None
        self.fresh = False
        self.last_attempt = float('-inf')
        self.target = None

    def observe(self, rows, now):
        pets = [r for r in rows if r.get('label') in PET_LABELS and not r.get('recently_seen')]
        if not pets:
            return
        at = max(r.get('at', now) for r in pets)
        if self.last_seen is None or at - self.last_seen > PET_GAP_SECONDS:
            if self.last_seen is None or at - self.last_seen >= PET_ABSENT_SECONDS:
                self.fresh = True
            self.since = at
        self.last_seen = max(self.last_seen or at, at)
        self.target = dict(max(pets, key=lambda r: r['box'][2] * r['box'][3]))

    def take(self, session_id, now, speech_marker, faces=()):
        if (not self.fresh or self.since is None or now - self.last_seen > PET_GAP_SECONDS
                or self.last_seen - self.since < PET_SETTLE_SECONDS or now - self.last_attempt < PET_COOLDOWN_SECONDS):
            return None
        from .proactive import Opportunity
        self.fresh, self.last_attempt = False, now
        return Opportunity(uuid4().hex, session_id, now, self.target['target_id'], None, tuple(faces),
                           speech_marker, False, 'pet_arrival',
                           pet={k: self.target.get(k) for k in ('target_id', 'label', 'box', 'at')})

    def seen_ago(self, now):
        return None if self.last_seen is None else now - self.last_seen


class Present:
    def __init__(self):
        self.lock = threading.RLock()
        self.recognized = {}     # person_id -> last face or supported-voice recognition
        self.visit_since = {}    # person_id -> when their current visit began
        self.spoke = {}          # person_id -> last supported speech
        self.face_at = float('-inf')     # Any eligible face.
        self.speech_at = float('-inf')   # Any trusted human speech.
        self.audience_seen = None        # (person_id, at): last evidenced private audience.
        self.company_at = float('-inf')  # When more than one person was last in evidence.
        self.pets = Pets()
        self.things = {}         # label -> {'at', 'position'} for recently seen objects.
        self.last_human = None   # {'person_id', 'at'}
        self.last_rex = None     # {'text', 'at', 'question', 'own_initiative'}
        self.recent_rex = deque(maxlen=RECENT_LINES)   # His last heard lines, oldest first.
        self.engaged = {}        # person_id -> last time Rex spoke with them in view or heard them
        self.last_look = None    # {'where', 'why', 'at'}: the last look Rex chose, and why.
        self.unanswered = 0      # Rex's own heard lines since a human last spoke.

    # People ------------------------------------------------------------------
    def _recognize(self, person, at):
        last = self.recognized.get(person)
        if last is None or at - last > STAYS_SECONDS:
            self.visit_since[person] = at
        self.recognized[person] = max(last or at, at)

    def saw_faces(self, faces, now):
        """Fresh camera faces. A tiny far face is not company."""
        with self.lock:
            for face in faces or ():
                at = face.get('at', now)
                if at is None or at > now + 1:
                    continue
                if (face.get('face_width_px') or 48) < 48:
                    continue
                self.face_at = max(self.face_at, at)
                if face.get('identity'):
                    self._recognize(face['identity'], at)

    def heard(self, person_id, at):
        """Trusted human speech; person_id only for a supported voice. Rex's lines are answered."""
        with self.lock:
            self.speech_at = max(self.speech_at, at)
            self.unanswered = 0
            self.last_human = {'person_id': person_id, 'at': at}
            if person_id:
                self._recognize(person_id, at)
                self.spoke[person_id] = max(self.spoke.get(person_id, at), at)
                self.engaged[person_id] = max(self.engaged.get(person_id, at), at)

    def here(self, person, now):
        """Is this person still here? None asks whether anyone is."""
        with self.lock:
            if not person:
                return now - max(self.face_at, self.speech_at) <= HERE_SECONDS
            recognized = self.recognized.get(person, float('-inf'))
            if now - recognized <= HERE_SECONDS:
                return True
            # Their face and voice can both go unrecognized while they stay (live October 1).
            # An unrecognized face or trusted speech, with nobody else recognized, continues
            # their visit for a bounded time after the last positive recognition.
            others = any(p != person and now - t <= HERE_SECONDS for p, t in self.recognized.items())
            return (now - recognized <= STAYS_SECONDS and not others and
                    now - max(self.face_at, self.speech_at) <= HERE_SECONDS)

    def alone_with(self, person, now):
        """Only this person has been in evidence lately: recognized within HERE_SECONDS, and no
        second face or other recognized person for COMPANY_SECONDS. Evidence for the state
        owner's speaker deduction; this module still names nobody as a speaker."""
        with self.lock:
            if now - self.recognized.get(person, float('-inf')) > HERE_SECONDS:
                return False
            if now - self.company_at < COMPANY_SECONDS:
                return False
            return not any(p != person and now - t <= COMPANY_SECONDS for p, t in self.recognized.items())

    def arriving(self, person, now):
        """A known person counts as arriving only when their visit just began and Rex has not
        already spoken with them in it. Someone seen or heard within STAYS_SECONDS never left,
        whatever the camera's track numbers say."""
        with self.lock:
            since = self.visit_since.get(person)
            if since is None:
                return True
            return now - since <= ARRIVAL_SECONDS and self.engaged.get(person, float('-inf')) < since

    def someone_around(self, now, since=float('-inf')):
        with self.lock:
            return now - max(self.face_at, self.speech_at, since) <= ALONE_SECONDS

    def last_seen(self, person):
        with self.lock:
            return self.recognized.get(person, float('-inf'))

    def forget(self, person_id):
        with self.lock:
            for table in (self.recognized, self.visit_since, self.spoke, self.engaged):
                table.pop(person_id, None)
            self.audience_seen = None   # A fresh recognition is needed after any forget.
            if self.last_human and self.last_human.get('person_id') == person_id:
                self.last_human = {**self.last_human, 'person_id': None}

    # Private audience -----------------------------------------------------------
    def note_audience(self, context, now):
        """Remember the private audience while evidence supports it, and offer it back to the
        memory gate for a short while after (conversation_memory.subject). A contradiction
        (several faces, somebody else recognized) ends the carry at once."""
        from . import conversation_memory as cm
        with self.lock:
            who = cm.evidenced_subject(context)
            faces = context.get('current_view', {}).get('faces', context.get('faces', []))
            if len(faces) > 1 or (self.audience_seen and cm.contradicted(context, self.audience_seen[0])):
                self.audience_seen, self.company_at = None, now
            if who:
                self.audience_seen = (who, now)
            context.pop('recent_audience', None)
            if self.audience_seen and now - self.company_at >= COMPANY_SECONDS:
                context['recent_audience'] = {'person_id': self.audience_seen[0],
                    'seconds_ago': round(now - self.audience_seen[1], 1),
                    'meaning': 'Last person recognized as the private audience. Keeps their memories usable through '
                               'brief camera gaps; not proof they are visible now or that they spoke.'}
            return context

    # Things ------------------------------------------------------------------
    def saw_things(self, rows, now):
        with self.lock:
            self.pets.observe(rows, now)
            for row in rows:
                if row.get('recently_seen') or not row.get('label'):
                    continue
                label = 'pet' if row['label'] in PET_LABELS else row['label']
                self.things[label] = {'at': row.get('at', now),
                                      'position': (row.get('image_position') or {}).get('description')}
            while len(self.things) > THINGS_KEPT:
                oldest = min(self.things, key=lambda k: self.things[k]['at'])
                self.things.pop(oldest)

    def pet_arrival(self, session_id, now, speech_marker, faces=()):
        with self.lock:
            return self.pets.take(session_id, now, speech_marker, faces)

    # Conversation clock --------------------------------------------------------
    def rex_said(self, text, at, *, own_initiative=False):
        """A line of Rex's that was actually heard (complete audible delivery)."""
        from .output import plain_text
        text = plain_text(text or '').strip()
        if not text:
            return
        with self.lock:
            self.last_rex = {'text': text[:300], 'at': at, 'question': text.endswith('?'),
                             'own_initiative': own_initiative}
            self.recent_rex.append({'text': text[:200], 'at': at})
            for person, seen in self.recognized.items():
                if at - seen <= ENGAGED_SECONDS:
                    self.engaged[person] = max(self.engaged.get(person, at), at)

    def rex_looked(self, where, why, at):
        """Rex chose to look somewhere (his own beat or a request), and why."""
        with self.lock:
            self.last_look = {'where': where, 'why': (why or '').strip()[:200], 'at': at}

    def beat_heard(self):
        with self.lock:
            self.unanswered += 1

    # The picture -----------------------------------------------------------------
    def describe(self, now, names=None, *, rex=None, wall=None):
        """Rex's sense of the moment in words. Names are display data for recognized people;
        wall is the local wall-clock datetime."""
        names = names or {}
        with self.lock:
            people = []
            for person, at in sorted(self.recognized.items(), key=lambda item: -item[1]):
                if not self.here(person, now):
                    continue
                seen = now - at
                status = ('recognized just now' if seen < 3 else
                          f'here; last recognized {ago(seen)} (probably just out of view or unrecognized)')
                people.append({'person_id': person, 'name': names.get(person), 'status': status,
                               'here_since': ago(now - self.visit_since.get(person, at))})
            unknown = now - self.face_at <= 3 and not any(now - t <= 3 for t in self.recognized.values())
            pet_ago = self.pets.seen_ago(now)
            things = [{'thing': label, 'seen': ago(now - row['at']), 'where': row['position']}
                      for label, row in sorted(self.things.items(), key=lambda item: -item[1]['at'])
                      if now - row['at'] <= 600]
            conversation = {}
            spoke = self.last_human['at'] if self.last_human else None
            quiet = now - max(spoke if spoke is not None else float('-inf'),
                              self.last_rex['at'] if self.last_rex else float('-inf'))
            quiet = None if quiet == float('inf') else quiet
            if self.last_human:
                who = names.get(self.last_human['person_id']) or 'someone'
                conversation['last_human_words'] = f'{who} spoke {ago(now - spoke)}'
            if self.last_rex:
                line = self.last_rex
                answered = spoke is not None and spoke > line['at']
                conversation['rex_last_line'] = {'text': line['text'], 'said': ago(now - line['at']),
                    'what_became_of_it': line_meaning(line, answered, now - line['at'])}
            conversation['quiet'] = quiet_meaning(quiet)
            if self.unanswered:
                conversation['unanswered_lines'] = (f'{self.unanswered} of his own lines in a row went unanswered: '
                                                    'people are busy or not in the mood to chat')
            earlier = list(self.recent_rex)[:-1] if self.last_rex else list(self.recent_rex)
            if earlier:
                conversation['his_earlier_lines'] = {
                    'lines': [{'said': ago(now - line['at']), 'text': line['text']} for line in earlier],
                    'meaning': 'Points, jokes and apologies he already made: never make them again, and never '
                               'bring back a mistake he already owned unless they do.'}
            picture = {'local_time': local_time(wall),
                       'people_here': people,
                       'unrecognized_face_in_view': unknown,
                       'pet': (None if pet_ago is None or pet_ago > 600 else
                               {'seen': ago(pet_ago), 'in_view': pet_ago <= PET_GAP_SECONDS}),
                       'things_seen': things,
                       'conversation': conversation,
                       'meaning': 'Rex\'s sense of the moment, kept by code. Times are in words because they '
                                  'matter as feelings: whether a moment has passed, whether someone is still '
                                  'around, whether a question is still hanging. "Here" means still around, not '
                                  'necessarily in view; it never says who is speaking.'}
            if rex:
                picture['rex'] = rex
            return picture
