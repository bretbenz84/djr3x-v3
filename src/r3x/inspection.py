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
