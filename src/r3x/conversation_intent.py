"""Bounded session intentions. Evidence and delivery never become biography."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import json
from uuid import uuid4


UPDATE_TOOLS = frozenset({'respond_with_intent', 'ask_face_question',
                         'use_memory_opportunity', 'conversation_feedback', 'remain_silent'})


def update_schema():
    text = lambda n: {'type': 'string', 'minLength': 1, 'maxLength': n}
    fields = {
        'intent_id': {'type': ['string', 'null']},
        'revision': {'type': 'integer', 'minimum': 0},
        'source_id': text(120),
        'status': {'type': 'string', 'enum': ['active', 'suspended', 'completed', 'abandoned']},
        'objective': {'type': ['string', 'null'], 'maxLength': 240},
        'openings': {'type': 'array', 'items': text(160), 'maxItems': 2},
        'target_ids': {'type': 'array', 'items': text(120), 'maxItems': 3},
        'memory_ids': {'type': 'array', 'items': text(120), 'maxItems': 8},
        'reason': text(160),
    }
    return {'type': ['object', 'null'], 'properties': fields,
            'required': list(fields), 'additionalProperties': False}


def validate_update(value):
    if value is None:
        return
    spec = update_schema()['properties']
    if not isinstance(value, dict) or value.keys() != spec.keys():
        raise ValueError('InvalidIntentFields')
    for key, item in value.items():
        if key == 'revision':
            valid = type(item) is int and item >= 0
        elif key in {'openings', 'target_ids', 'memory_ids'}:
            valid = (isinstance(item, list) and len(item) <= spec[key]['maxItems']
                     and all(isinstance(s, str) and 0 < len(s.strip()) <= spec[key]['items']['maxLength'] for s in item)
                     and len(set(item)) == len(item))
        elif item is None:
            valid = key in {'intent_id', 'objective'}
        else:
            valid = isinstance(item, str) and 0 < len(item.strip()) <= spec[key].get('maxLength', 120)
        if not valid:
            raise ValueError('InvalidIntentValue')
    if value['status'] not in spec['status']['enum']:
        raise ValueError('InvalidIntentStatus')
    terminal = value['status'] in {'completed', 'abandoned'}
    if terminal:
        if value['objective'] is not None or any(value[k] for k in ('openings', 'target_ids', 'memory_ids')):
            raise ValueError('TerminalIntentHasContent')
    elif not value['objective']:
        raise ValueError('IntentObjectiveRequired')


def faces_for(context):
    return context.get('current_view', {}).get('faces', context.get('faces', []))


def audience(context):
    """A camera track is never substituted for a voice track or persistent person."""
    faces = faces_for(context)
    if faces:
        return tuple(sorted((f['track_id'], f.get('identity'), f.get('face_epoch', 0)) for f in faces))
    speaker = context.get('speaker', {})
    if speaker.get('track_id'):
        return (('voice:' + speaker['track_id'], speaker.get('person_id'), 0),)
    return ()


def targets(context):
    result = []
    for face in faces_for(context):
        result.append('face:' + face['track_id'])
        if face.get('identity'):
            result.append('person:' + face['identity'])
    speaker = context.get('speaker', {})
    if speaker.get('track_id'):
        result.append('voice:' + speaker['track_id'])
    if speaker.get('person_id'):
        result.append('person:' + speaker['person_id'])
    return list(dict.fromkeys(result))[:6]


@dataclass
class IntentRecord:
    id: str
    status: str
    objective: str | None
    openings: list[str]
    target_ids: list[str]
    memory_ids: list[str]
    source_id: str
    audience: tuple
    last_activity: float
    last_seen: float
    reason: str
    delivery: dict = field(default_factory=lambda: {'status': 'not_delivered', 'heard': False})


class ConversationIntent:
    def __init__(self, trace):
        self.trace = trace
        self.revision = 0
        self.record: IntentRecord | None = None
        self.recent = deque(maxlen=8)
        self.requests = {}  # bounded results; an old source/revision cannot revive an evicted request

    def _changed(self, reason, source_id=None):
        self.revision += 1
        receipt = {'revision': self.revision, 'reason': reason, 'source_id': source_id}
        self.recent.append(receipt)
        self.trace.emit('conversation_intent_transition', **receipt,
                        intent_id=self.record.id if self.record else None,
                        status=self.record.status if self.record else 'cleared')

    def clear(self, reason):
        if self.record or self.recent or self.requests:
            self.record = None
            self.recent.clear()
            self.requests.clear()
            self.revision += 1
            self.trace.emit('conversation_intent_transition', revision=self.revision,
                            reason=reason, status='cleared', intent_id=None)

    def suspend(self, reason):
        if self.record and self.record.status == 'active':
            self.record.status = 'suspended'
            self.record.reason = reason
            self._changed(reason)

    def refresh(self, context, memory_ids, *, human=False):
        row = self.record
        if not row:
            return
        now = self.trace.clock()
        # Expire before refreshing activity: arriving input cannot revive an old intention.
        if now - row.last_activity >= 900:
            self.clear('expired')
            return
        if row.status in {'completed', 'abandoned'}:
            return
        current = audience(context)
        selected = set(row.target_ids)
        watched = {member for member in row.audience
                   if ('face:' + member[0] in selected or member[0] in selected
                       or (member[1] and 'person:' + member[1] in selected))}
        # A remaining bystander cannot keep a departed visitor's intention alive.
        watched = watched or set(row.audience)
        present = bool(watched & set(current))
        if present:
            row.last_seen = now
        if current == row.audience and current:
            if human:
                row.last_activity = now
        else:
            self.suspend('audience_changed_or_stale')
            if not present and now - row.last_seen >= 30:
                self.clear('audience_absent')
                return
        if not set(row.memory_ids) <= set(memory_ids):
            self.clear('memory_no_longer_eligible')
            return
        participation = context.get('participation', {})
        if participation.get('initiative_quiet'):
            self.suspend('initiative_quiet')
        elif any(f['action'] == 'yield_floor' for f in participation.get('feedback', [])):
            self.suspend('human_floor')

    def context(self, context):
        row = self.record
        # Changed audiences must never receive an old group's free-text intention.
        visible = row is not None and row.audience == audience(context) and bool(row.audience)
        current = None
        if visible:
            current = {k: deepcopy(getattr(row, k)) for k in
                       ('id', 'status', 'objective', 'openings', 'target_ids', 'memory_ids', 'source_id', 'reason', 'delivery')}
        result = {'revision': self.revision, 'intent_id': row.id if row else None,
                  'current': current, 'target_ids': targets(context),
                  'source_id': context.get('event_id') or context.get('source_turn_id')}
        # Only share receipts when their original audience remains eligible.
        if visible:
            result['recent'] = list(self.recent)
            while result['recent'] and len(json.dumps(result)) > 2000:
                result['recent'].pop(0)
        if len(json.dumps(result)) > 2000:
            result.pop('recent', None)
            result['current'] = None
        return result

    def prepare(self, value, context, memory_ids):
        validate_update(value)
        if value is None:
            return None
        if context.get('frozen_evaluation'):
            raise ValueError('FrozenEvaluationDisallowsMutation')
        offered = context.get('conversation_intent', {})
        if value['revision'] != self.revision or offered.get('revision') != self.revision:
            raise ValueError('StaleIntentRevision')
        if value['intent_id'] != (self.record.id if self.record else None):
            raise ValueError('IntentIdMismatch')
        if value['source_id'] != (context.get('event_id') or context.get('source_turn_id')):
            raise ValueError('IntentSourceMismatch')
        if not set(value['target_ids']) <= set(targets(context)):
            raise ValueError('IntentTargetNotOffered')
        offered_memories = {r['claim_id'] for r in context.get('memory_opportunities', [])}
        if not set(value['memory_ids']) <= set(memory_ids) & offered_memories:
            raise ValueError('IntentMemoryNotOffered')
        if not audience(context):
            raise ValueError('IntentAudienceUnavailable')
        if self.record is None and value['status'] in {'completed', 'abandoned'}:
            raise ValueError('NoIntentToFinish')
        if self.record and self.record.status in {'completed', 'abandoned'} and value['status'] in {'active', 'suspended'}:
            # A fresh source may start a new visit intention, never an automatic revival.
            if value['source_id'] == self.record.source_id:
                raise ValueError('IntentNeedsNewSource')
        now = self.trace.clock()
        row = IntentRecord(self.record.id if self.record else uuid4().hex,
                           value['status'], value['objective'], list(value['openings']),
                           list(value['target_ids']), list(value['memory_ids']), value['source_id'],
                           audience(context), self.record.last_activity if self.record else now,
                           now, value['reason'])
        # Reserve room for fixed context fields and an output receipt.
        preview = {k: v for k, v in vars(row).items() if k not in {'audience', 'last_activity', 'last_seen'}}
        if len(json.dumps(preview)) + len(json.dumps(targets(context))) > 1500:
            raise ValueError('IntentContextTooLarge')
        return row

    def commit(self, row):
        if row is not None:
            if self.record and self.record.audience != row.audience:
                self.recent.clear()
            self.record = row
            self._changed(row.reason, row.source_id)

    def delivered(self, source_id, turn_id, delivery):
        row = self.record
        if row is None or row.source_id != source_id:
            return
        heard = delivery.medium == 'audio' and bool(delivery.completed_text)
        row.delivery = {'turn_id': turn_id, 'status': delivery.status,
                        'medium': delivery.medium, 'heard': heard}
        if heard:
            row.last_activity = self.trace.clock()
        self.trace.emit('conversation_intent_delivery', intent_id=row.id, **row.delivery)
