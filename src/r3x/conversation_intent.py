"""Bounded session intentions. Evidence and delivery never become biography."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import json
from uuid import uuid4


UPDATE_TOOLS = frozenset({'respond_with_intent', 'ask_face_question',
                         'use_memory_opportunity', 'conversation_feedback', 'remain_silent'})


def interests_schema():
    fields = {
        'id': {'type': ['string', 'null']},
        'topic': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'curiosity': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'next_action': {'type': 'string', 'minLength': 1, 'maxLength': 160},
        'kind': {'type': 'string', 'enum': ['conversation', 'observation', 'memory', 'character_idea']},
        'evidence_ids': {'type': 'array', 'items': {'type': 'string', 'maxLength': 120}, 'maxItems': 4},
        'status': {'type': 'string', 'enum': ['active', 'waiting', 'resolved', 'abandoned']},
    }
    return {'type': ['array', 'null'], 'maxItems': 3, 'items': {'type': 'object',
            'properties': fields, 'required': list(fields), 'additionalProperties': False}}


def validate_interests(items):
    if items is None:
        return
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError('InvalidWorkingInterests')
    for item in items:
        fields = interests_schema()['items']['properties']
        if not isinstance(item, dict) or item.keys() != fields.keys():
            raise ValueError('InvalidWorkingInterestFields')
        if item['id'] is not None and (not isinstance(item['id'], str) or not 0 < len(item['id']) <= 120):
            raise ValueError('InvalidWorkingInterestId')
        for key in ('topic', 'curiosity', 'next_action'):
            if not isinstance(item[key], str) or not 0 < len(item[key].strip()) <= 160:
                raise ValueError('InvalidWorkingInterestText')
        for key in ('kind', 'status'):
            if item[key] not in fields[key]['enum']:
                raise ValueError('InvalidWorkingInterestKindOrStatus')
        refs = item['evidence_ids']
        if (not isinstance(refs, list) or len(refs) > 4 or
                any(not isinstance(ref, str) or not 0 < len(ref) <= 120 for ref in refs) or len(set(refs)) != len(refs)):
            raise ValueError('InvalidWorkingInterestEvidence')


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
        'interests': interests_schema(),
        'scope': {'type': 'string', 'enum': ['audience', 'general']},
    }
    return {'type': ['object', 'null'], 'properties': fields,
            'required': list(fields), 'additionalProperties': False}


def validate_update(value):
    if value is None:
        return
    spec = update_schema()['properties']
    if not isinstance(value, dict) or (value.keys() - spec.keys() or spec.keys() - value.keys() - {'interests', 'scope'}):
        raise ValueError('InvalidIntentFields')
    for key, item in value.items():
        if key == 'interests':
            validate_interests(item)
            continue
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
    if value.get('scope', 'audience') not in {'audience', 'general'}:
        raise ValueError('InvalidIntentScope')
    if value.get('scope') == 'general' and (value['target_ids'] or value['memory_ids']):
        raise ValueError('GeneralIntentHasPersonalScope')
    if value['status'] not in spec['status']['enum']:
        raise ValueError('InvalidIntentStatus')
    terminal = value['status'] in {'completed', 'abandoned'}
    if terminal:
        if value['objective'] is not None or any(value[k] for k in ('openings', 'target_ids', 'memory_ids')) or value.get('interests'):
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
    interests: list[dict] = field(default_factory=list)
    closed_interests: list[dict] = field(default_factory=list)
    delivery: dict = field(default_factory=lambda: {'status': 'not_delivered', 'heard': False})


class ConversationIntent:
    def __init__(self, trace, invalidate_schedule=None):
        self.trace = trace
        self.invalidate_schedule = invalidate_schedule
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
        if self.invalidate_schedule and reason != 'audience_absent':
            self.invalidate_schedule(reason)
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
            # Missing visual evidence hides scoped notes, not general consideration.
            if self.invalidate_schedule and reason != 'audience_changed_or_stale':
                self.invalidate_schedule(reason)

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
        generic = not row.audience and not row.target_ids and not row.memory_ids
        present = generic or bool(watched & set(current))
        if present:
            row.last_seen = now
        if generic or current == row.audience and current:
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
        visible = row is not None and (row.audience == audience(context) or
            not row.audience and not row.target_ids and not row.memory_ids)
        current = None
        if visible:
            current = {k: deepcopy(getattr(row, k)) for k in
                       ('id', 'status', 'objective', 'openings', 'target_ids', 'memory_ids', 'source_id', 'reason', 'delivery', 'interests', 'closed_interests')}
            current['scope'] = 'audience' if row.audience else 'general'
        result = {'revision': self.revision, 'intent_id': row.id if row else None,
                  'current': current, 'target_ids': targets(context),
                  'source_id': context.get('event_id') or context.get('source_turn_id')}
        # Only share receipts when their original audience remains eligible.
        if visible:
            result['recent'] = list(self.recent)
            while result['recent'] and len(json.dumps(result)) > 6000:
                result['recent'].pop(0)
        if len(json.dumps(result)) > 6000:
            result.pop('recent', None)
            result['current'] = None
        return result

    def working_signature(self):
        """State change for resource pacing, not a semantic novelty classifier."""
        row = self.record
        if row is None:
            return None
        return json.dumps({'status': row.status, 'objective': row.objective,
            'openings': row.openings, 'targets': row.target_ids, 'memories': row.memory_ids,
            'interests': [{k: item[k] for k in ('topic', 'curiosity', 'next_action', 'kind', 'status')}
                          for item in row.interests]}, sort_keys=True)

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
        if not audience(context) and (value['target_ids'] or value['memory_ids']):
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
                           () if value.get('scope') == 'general' else audience(context), self.record.last_activity if self.record else now,
                           now, value['reason'])
        existing = self.record if self.record and self.record.audience == row.audience else None
        offered_interests = (offered.get('current') or {}).get('interests', [])
        previous = {item['id']: item for item in offered_interests}
        items = value.get('interests')
        row.interests = deepcopy(existing.interests) if existing else []
        row.closed_interests = deepcopy(existing.closed_interests) if existing else []
        if items is not None:
            evidence = {value['source_id']}
            evidence.update(r['cue_id'] for r in context.get('observations', []))
            evidence.update(r['id'] for r in context.get('visual_memory', []))
            evidence.update(value['memory_ids'])
            human_source = context.get('source_turn_id') or context.get('opportunity', {}).get('source_turn_id')
            if human_source:
                evidence.add(human_source)
            new = []
            used = set()
            for item in items:
                identifier = item['id']
                if identifier is not None and (identifier not in previous or identifier in used):
                    raise ValueError('WorkingInterestNotOffered')
                prior = previous.get(identifier)
                refs = set(item['evidence_ids'])
                prior_refs = set(prior['evidence_ids']) if prior and prior['kind'] == item['kind'] else set()
                if not refs <= evidence | prior_refs or (item['kind'] != 'character_idea' and not refs):
                    raise ValueError('WorkingInterestEvidenceNotOffered')
                if not row.audience and item['kind'] in {'conversation', 'memory'}:
                    raise ValueError('GeneralInterestHasPersonalSource')
                if item['kind'] == 'conversation' and not refs & ({human_source} | prior_refs):
                    raise ValueError('WorkingInterestHumanSourceRequired')
                if item['kind'] == 'observation' and not refs & (
                        {r['cue_id'] for r in context.get('observations', [])} |
                        {r['id'] for r in context.get('visual_memory', [])} | prior_refs):
                    raise ValueError('WorkingInterestObservationRequired')
                if item['kind'] == 'memory' and not refs & set(value['memory_ids']):
                    raise ValueError('WorkingInterestMemoryNotOffered')
                identifier = identifier or uuid4().hex
                used.add(identifier)
                saved = {**deepcopy(item), 'id': identifier, 'source_id': value['source_id'],
                         'delivery': deepcopy(prior['delivery']) if prior else {'status': 'not_delivered'}}
                if saved['status'] in {'resolved', 'abandoned'}:
                    row.closed_interests.append(saved)
                else:
                    new.append(saved)
            for identifier, old in previous.items():
                if identifier not in used:
                    row.closed_interests.append({**deepcopy(old), 'status': 'abandoned'})
            row.interests = new
        if value['status'] in {'completed', 'abandoned'}:
            row.closed_interests.extend({**item, 'status': value['status'] if value['status']=='abandoned' else 'resolved'}
                                        for item in row.interests)
            row.interests = []
        row.closed_interests = row.closed_interests[-3:]
        # Reserve room for fixed context fields and an output receipt.
        preview = {k: v for k, v in vars(row).items() if k not in {'audience', 'last_activity', 'last_seen'}}
        while row.closed_interests and len(json.dumps(preview)) + len(json.dumps(targets(context))) > 5200:
            row.closed_interests.pop(0)
        if len(json.dumps(preview)) + len(json.dumps(targets(context))) > 5200:
            raise ValueError('IntentContextTooLarge')
        return row

    def commit(self, row):
        if row is not None:
            if self.record and self.record.audience != row.audience:
                self.recent.clear()
            self.record = row
            self._changed(row.reason, row.source_id)
            if row.status in {'completed', 'abandoned'} and self.invalidate_schedule:
                self.invalidate_schedule('intent_' + row.status)

    def delivered(self, source_id, turn_id, delivery):
        row = self.record
        if row is None or row.source_id != source_id:
            return
        heard = delivery.medium == 'audio' and bool(delivery.completed_text)
        row.delivery = {'turn_id': turn_id, 'status': delivery.status,
                        'medium': delivery.medium, 'heard': heard}
        for item in row.interests:
            if item['source_id'] == source_id:
                item['delivery'] = deepcopy(row.delivery)
        if heard:
            row.last_activity = self.trace.clock()
        self.trace.emit('conversation_intent_delivery', intent_id=row.id, **row.delivery)
