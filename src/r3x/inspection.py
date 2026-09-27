"""Motion -> settled view -> fresh image, with injected simulation providers only."""
from dataclasses import dataclass
from .contracts import ProviderFailure, Cancelled


@dataclass(frozen=True)
class InspectionRequest:
    request_id: str
    direction: str
    question: str
    view_epoch: int
    requested_at: float
    captured_after: float | None = None  # Physical base motion: only frames after it settled.


@dataclass(frozen=True)
class MovementReceipt:
    request_id: str
    direction: str
    view_epoch: int
    completed_at: float
    status: str = 'simulated'


@dataclass(frozen=True)
class InspectionFrame:
    request_id: str
    direction: str
    view_epoch: int
    captured_at: float
    image: object


class Inspection:
    """Explicitly injected move/wait/capture providers; never opened by live startup.

    Providers must honor cancellation and the absolute deadline passed to them.
    The waiter must return only after the requested settling deadline. No sleep
    duration is selected by the LLM. Real motor/camera adapters remain unimplemented.
    """
    def __init__(self, clock, move, wait_until, capture, *, settle_seconds=.5):
        import math
        if type(settle_seconds) not in {int,float} or not math.isfinite(settle_seconds) or not 0<settle_seconds<=5:
            raise ValueError('InvalidSettleTime')
        self.clock, self.move, self.wait_until, self.capture = clock, move, wait_until, capture
        self.settle_seconds, self.started = settle_seconds, False

    def start(self):
        self.started = True

    def close(self):
        self.started = False

    def execute(self, request, cancel, valid):
        deadline = request.requested_at+15
        result = {'request_id':request.request_id,'direction':request.direction,
                  'view_epoch':request.view_epoch,'medium':'simulation','stage':'movement',
                  'motion_status':'not_completed','status':'failed'}
        def check():
            cancel.check()
            if not valid():raise ProviderFailure('InspectionSuperseded')
            if self.clock()>=deadline:raise ProviderFailure('InspectionTimeout')
        try:
            check()
            if not self.started:raise ProviderFailure('InspectionNotStarted')
            receipt = self.move(request,cancel,deadline)
            check()
            if (not isinstance(receipt,MovementReceipt) or receipt.request_id!=request.request_id or
                receipt.direction!=request.direction or receipt.view_epoch!=request.view_epoch or
                receipt.status!='simulated' or not request.requested_at<=receipt.completed_at<=self.clock()):
                raise ProviderFailure('MovementNotCompleted')
            result.update(motion_status='simulated',motion_completed_at=receipt.completed_at,stage='settling')
            settled_at = receipt.completed_at+self.settle_seconds
            self.wait_until(settled_at,cancel,deadline)
            check()
            if self.clock()<settled_at:raise ProviderFailure('MovementNotSettled')
            result.update(settled_at=settled_at,stage='capture')
            frame = self.capture(request,cancel,deadline)
            check()
            if (not isinstance(frame,InspectionFrame) or frame.request_id!=request.request_id or
                frame.direction!=request.direction or frame.view_epoch!=request.view_epoch or
                not settled_at<frame.captured_at<=self.clock() or self.clock()-frame.captured_at>2 or
                frame.image.at!=frame.captured_at or not frame.image.data_url.startswith('data:image/png;base64,') or
                len(frame.image.data_url)>1_500_000):
                raise ProviderFailure('InspectionImageStaleOrMismatched')
            result.update(status='simulated',stage='complete',captured_at=frame.captured_at,
                          frame_id=frame.image.frame_id,reason='Synthetic image after simulated motion; no physical inspection occurred.')
            return result,(frame.image,)
        except ProviderFailure as exc:
            result['reason']=str(exc)
            return result,()
        except Cancelled:
            result.update(status='cancelled',reason='TurnCancelled')
            return result,()
        except Exception as exc:
            result['reason']=type(exc).__name__
            return result,()


class CurrentView:
    """Reuse the shared scene encoder; no motor access or second camera consumer."""
    directions = ('current',)

    def __init__(self, scene, clock=None):
        import time
        from .providers import Capability
        from threading import Event
        self.scene, self.clock = scene, clock or time.monotonic
        self.stop, self.started = Event(), False
        self.capability = Capability('current_view','shared camera scene cache',
            'tool-selected current-view visual questions',None,(),('one <=480px image per requested inspection',),required=False)

    def start(self):
        self.stop.clear();self.started=True

    def cancel(self):
        self.stop.set()

    def close(self):
        self.cancel();self.started=False

    def execute(self, request, cancel, valid):
        moved = request.captured_after is not None
        result={'request_id':request.request_id,'direction':request.direction,'view_epoch':request.view_epoch,
                'medium':'camera','stage':'capture','motion_status':'base_motion_finished' if moved else 'not_requested','status':'failed'}
        if not self.started or self.stop.is_set():
            return {**result,'reason':'CurrentViewNotStarted'},()
        if request.direction!='current':
            return {**result,'reason':'MotionUnavailable'},()
        deadline=self.clock()+(3 if moved else 2)
        import time
        wall_deadline=time.monotonic()+(3 if moved else 2)
        while True:
            cancel.check()
            if not valid():return {**result,'reason':'InspectionSuperseded'},()
            if self.stop.is_set():return {**result,'reason':'CurrentViewStopped'},()
            try:
                snapshot=self.scene.snapshot(True)
            except Exception as exc:
                return {**result,'reason':type(exc).__name__},()
            if snapshot:
                image=snapshot[1][-1]
                age=self.clock()-image.at
                if 0<=age<=2 and (not moved or image.at>=request.captured_after):
                    from hashlib import sha256
                    return {**result,'image_digest':sha256(image.data_url.encode()).hexdigest(),'status':'observed','stage':'complete','frame_id':image.frame_id,
                        'captured_at':image.at,'capture_age_seconds':age,'shared_frame':True,
                        'reason':'Camera view captured after the base stopped moving.' if moved else
                                 'Current camera view; no movement requested or performed.'},(image,)
            if self.clock()>=deadline or time.monotonic()>=wall_deadline:
                return {**result,'reason':'FreshCurrentViewUnavailable'},()
            cancel.event.wait(.05)
