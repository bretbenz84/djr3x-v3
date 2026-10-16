"""One immutable latest-frame mailbox shared by independently paced consumers."""
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class Frame:
    frame_id: str
    camera_id: str
    session_id: str
    at: float
    width: int
    height: int
    pixels: bytes


# Faces, scene, objects and places. October 15: the limit was three, so room recognition
# was refused at every start from the day it landed (October 11) and never ran live.
CONSUMERS = 4


class FrameHub:
    """One pending frame plus one in-flight frame per registered consumer (max CONSUMERS).

    Immutable byte storage is shared, not copied per subscriber. Consumers replace
    pending work and may hold only their current frame. At 1080p five shared frames are
    29.7 MiB. No historical full-resolution queue.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.latest = None
        self.closed = False
        self.consumers = set()

    def register(self,name):
        with self.condition:
            if name in self.consumers or len(self.consumers)>=CONSUMERS: raise ValueError('VisualConsumerCapacity')
            self.consumers.add(name)

    def publish(self,frame):
        if not isinstance(frame.pixels,bytes) or len(frame.pixels)!=frame.width*frame.height*3 or not 0<frame.width*frame.height<=1920*1080:
            raise ValueError('InvalidBoundedBGRFrame')
        with self.condition:
            if not self.closed:
                self.latest = frame
                self.condition.notify_all()

    def peek(self):
        """Newest frame without consuming a consumer slot (deliberate looks)."""
        with self.condition:
            return None if self.closed else self.latest

    def take(self,name,after=None,timeout=.5):
        with self.condition:
            if name not in self.consumers: raise ValueError('UnregisteredVisualConsumer')
            self.condition.wait_for(lambda:self.closed or (self.latest and self.latest.frame_id!=after),timeout)
            return None if self.closed or not self.latest or self.latest.frame_id==after else self.latest

    def close(self):
        with self.condition:
            self.closed = True
            self.latest = None
            self.condition.notify_all()
