"""Session social invitations; elapsed time permits review, never infers an answer."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4
from .conversation_intent import audience

TOOLS = frozenset({'respond_with_intent','remain_silent','conversation_feedback',
                   'respond_with_gesture','animate_lights','use_observation',
                   'use_visual_memory','use_memory_opportunity'})


def update_schema():
    fields = {
        'source_id': {'type':'string','minLength':1,'maxLength':120},
        'revision': {'type':'integer','minimum':0},
        'invitation_id': {'type':['string','null']},
        'action': {'type':'string','enum':['open','answered','declined','superseded','lapsed']},
        'topic': {'type':['string','null'],'maxLength':160},
        'scope': {'type':['string','null'],'enum':['general','audience',None]},
    }
    return {'type':['object','null'],'properties':fields,'required':list(fields),'additionalProperties':False}


def validate_update(value):
    if value is None:return
    fields=update_schema()['properties']
    if not isinstance(value,dict) or value.keys()!=fields.keys():raise ValueError('InvalidInvitationFields')
    if type(value['revision']) is not int or value['revision']<0:raise ValueError('InvalidInvitationRevision')
    for key,limit in (('source_id',120),('invitation_id',120),('topic',160)):
        item=value[key]
        if item is None and key!='source_id':continue
        if not isinstance(item,str) or not 0<len(item.strip())<=limit:raise ValueError('InvalidInvitationText')
    if value['action'] not in fields['action']['enum'] or value['scope'] not in fields['scope']['enum']:
        raise ValueError('InvalidInvitationAction')
    if value['action']=='open':
        if value['invitation_id'] is not None or not value['topic'] or value['scope'] is None:
            raise ValueError('InvalidInvitationOpening')
    elif not value['invitation_id'] or value['topic'] is not None or value['scope'] is not None:
        raise ValueError('InvalidInvitationTransition')


@dataclass(frozen=True)
class Proposal:
    source_id: str
    revision: int
    invitation_id: str | None
    action: str
    topic: str | None
    scope: str | None
    audience: tuple
    text: str | None
    review_delay: float | None


class Invitations:
    def __init__(self,trace):
        self.trace=trace
        self.revision=0
        self.pending=None
        self.recent=deque(maxlen=6)

    def _emit(self,*args,**kwargs):
        if self.trace:self.trace.emit(*args,**kwargs)

    @staticmethod
    def visible(row, context):
        return row['scope']=='general' or row['audience']==audience(context)

    def context(self,context,now):
        pending=self.pending if self.pending and self.visible(self.pending,context) else None
        return {'source_id':context.get('event_id') or context.get('source_turn_id'),
                'revision':self.revision,'pending':deepcopy(pending),
                'review_due':bool(pending and now>=pending['review_at']),
                'recent':[deepcopy(r) for r in self.recent if self.visible(r,context)],
                'meaning':'Only completed audible invitations are waiting. Review time does not mean rejection, disinterest or an answer. Close explicitly and retain that it was asked. General scope is impersonal only; private content stays audience scoped. Opening a new invitation explicitly supersedes the previous one.'}

    def waiting(self,context,now):
        return bool(self.pending and self.visible(self.pending,context) and now<self.pending['review_at'])

    def prepare(self,value,context,text,schedule,now):
        validate_update(value)
        if value is None:return None
        if context.get('frozen_evaluation'):raise ValueError('FrozenEvaluationDisallowsMutation')
        offered=context.get('invitations',{})
        source=context.get('event_id') or context.get('source_turn_id')
        if value['source_id']!=source or offered.get('source_id')!=source:raise ValueError('InvitationSourceMismatch')
        if value['revision']!=self.revision or offered.get('revision')!=self.revision:raise ValueError('StaleInvitation')
        if not context.get('trigger') and not context.get('transcript_trusted'):raise ValueError('UntrustedInvitationSource')
        delay=None
        if value['action']=='open':
            if not isinstance(text,str) or not text.strip():raise ValueError('InvitationRequiresSpeech')
            if schedule is None or schedule.trigger!='after_speech':raise ValueError('InvitationRequiresScheduledReview')
            delay=schedule.delay_seconds
            if value['scope']=='audience' and not audience(context):raise ValueError('InvitationAudienceUnavailable')
        else:
            current=offered.get('pending')
            if (not self.pending or not current or current['id']!=value['invitation_id'] or
                    self.pending['id']!=value['invitation_id'] or not self.visible(self.pending,context)):
                raise ValueError('InvitationUnavailable')
            if value['action'] in {'answered','declined','superseded'} and context.get('trigger'):
                raise ValueError('InvitationNeedsHumanSource')
            if value['action']=='lapsed' and now<self.pending['review_at']:
                raise ValueError('InvitationReviewNotDue')
        return Proposal(**value,audience=audience(context),text=text,review_delay=delay)

    def _close(self,status,source,now):
        row={**self.pending,'status':status,'closed_at':now,'closed_source_id':source}
        self.recent.append(row);self.pending=None
        self._emit('invitation_transition',invitation_id=row['id'],status=status,source_id=source,revision=self.revision)

    def feedback(self,action,reference,source,context,now):
        if (self.pending and self.pending['source_id']==reference and self.visible(self.pending,context) and
                action in {'invitation_answered','invitation_declined'}):
            self.revision+=1
            self._close(action.removeprefix('invitation_'),source,now)
            return True
        return False

    def invalidate(self,reason,erase=False):
        self.revision+=1
        if erase:self.pending=None;self.recent.clear()
        elif self.pending:self._close('interrupted',reason,self.trace.clock() if self.trace else 0.)
        self._emit('invitation_invalidated',reason=reason,revision=self.revision)

    def transition(self,proposal,now):
        if (proposal.action=='open' or proposal.revision!=self.revision or not self.pending or
                self.pending['id']!=proposal.invitation_id):return False
        self.revision+=1
        self._close(proposal.action,proposal.source_id,now)
        return True

    def finish(self,proposal,delivery,now,valid=True):
        if proposal is None:return False
        if not valid or proposal.revision!=self.revision or delivery.status not in {'completed','suppressed'} or delivery.error:
            self._emit('invitation_not_committed',reason='stale_cancelled_failed_or_held',source_id=proposal.source_id)
            return False
        if proposal.action=='open':
            from .output import plain_text
            if (delivery.status!='completed' or delivery.medium!='audio' or not delivery.completed_text or
                    plain_text(proposal.text).split()!=' '.join(delivery.completed_text).split()):
                self._emit('invitation_not_committed',reason='not_fully_heard',source_id=proposal.source_id)
                return False
            if self.pending:self._close('superseded',proposal.source_id,now)
            self.revision+=1
            self.pending={'id':uuid4().hex,'source_id':proposal.source_id,'status':'waiting',
                'topic':proposal.topic,'scope':proposal.scope,'audience':proposal.audience,
                'heard_text':' '.join(delivery.completed_text),'delivered_at':now,
                'review_at':now+proposal.review_delay}
            self._emit('invitation_transition',invitation_id=self.pending['id'],status='waiting',
                source_id=proposal.source_id,revision=self.revision,review_at=self.pending['review_at'])
        else:
            return self.transition(proposal,now)
        return True
