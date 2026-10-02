"""Session-only conversational action continuity; no autonomous task execution."""
from collections import deque
from copy import deepcopy


class Actions:
    def __init__(self) -> None:
        self.active: dict | None = None
        self.recent: deque[dict] = deque(maxlen=8)

    def begin(self, request_id: str, kind: str, purpose: str, source_turn_id: str, audience: str, now: float) -> None:
        if self.active is not None:raise ValueError('ActionAlreadyActive')
        self.active = {'request_id':request_id,'kind':kind,'purpose':purpose,
                       'source_turn_id':source_turn_id,'audience':audience,'started_at':now,
                       'stage':'executing','executor':None,'resume_policy':'never_automatic'}

    def observed(self, result: dict) -> None:
        if self.active is None:return
        keys=('images_acquired','status','outcome','stage','reason','medium','motion_status','motion_completed_at','settled_at','captured_at','frame_id','changed_channels')
        self.active['executor']={key:result[key] for key in keys if key in result}
        self.active['stage']='responding'

    def finish(self, delivery, now: float) -> dict | None:
        if self.active is None:return None
        row=self.active;self.active=None
        row.update(stage='finished',ended_at=now,turn_status=delivery.status,
                   speech={'status':delivery.status,'medium':delivery.medium,'heard_text':' '.join(delivery.completed_text)},
                   gestures=deepcopy(delivery.gestures))
        row['disposition']='abandoned' if delivery.status in {'interrupted','failed'} else 'finished'
        if row['executor'] is None and delivery.gestures:
            row['executor']=deepcopy(next((g for g in delivery.gestures if g['request_id']==row['request_id']),None))
        self.recent.append(row)
        return deepcopy(row)

    def motion_observed(self, result: dict) -> None:
        self.observed(result)
        if self.active is not None:
            # An optional inspection must not overwrite the movement's own receipt.
            self.active['motion'] = deepcopy(self.active['executor'])

    def context(self, audience: str) -> dict:
        return {'active':deepcopy(self.active) if self.active and self.active['audience']==audience else None,
                'recent':deepcopy([r for r in self.recent if r['audience']==audience]),
                'meaning':'Purpose and actual executor/speech outcomes are separate. Interrupted work is abandoned, never queued for automatic resumption. Follow the current user topic; only choose a fresh action if still relevant. A completed move or capture does not mean its answer was heard.'}

    def clear(self) -> None:
        self.active=None;self.recent.clear()
