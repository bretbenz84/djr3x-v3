"""Conservative within-capture change proposals from existing acoustic windows.

These boundaries are approximate, not human labels or proof of no overlap.
Derived turns cannot enroll voices or write personal facts.
"""
from dataclasses import replace

from .speakers import cosine


def change_boundaries(observation):
    windows = observation.windows
    cuts = []
    # Require repeated internally compatible evidence on BOTH sides of an abrupt
    # change. A low all-pairs minimum alone also occurs in single-speaker speech.
    for i in range(1, len(windows)-2):
        before, left, right, after = windows[i-1:i+3]
        if (cosine(before[2],left[2]) >= .55 and cosine(right[2],after[2]) >= .55
                and cosine(left[2],right[2]) < .35
                and cosine(before[2],after[2]) < .35):
            cut = (left[0]+left[1]+right[0]+right[1])/4
            if not cuts or cut-cuts[-1] >= 1.:
                cuts.append(cut)
        if len(cuts)==3:
            break
    return tuple(cuts)


def split_capture(capture, boundaries):
    if not boundaries:
        return (capture,)
    # Byte boundaries preserve every original PCM sample exactly once.
    total = len(capture.pcm)//2
    points = [0]+sorted({int(t*capture.sample_rate) for t in boundaries if 0<t<total/capture.sample_rate})+[total]
    result = []
    for i,(start,end) in enumerate(zip(points,points[1:]),1):
        a,b = start/capture.sample_rate,end/capture.sample_rate
        intervals = tuple((max(a,x)-a,min(b,y)-a) for x,y in capture.voiced_intervals if min(b,y)>max(a,x))
        result.append(replace(capture,capture_id=f"{capture.capture_id}-part{i}",pcm=capture.pcm[start*2:end*2],
            started_at=capture.started_at+a, ended_at=capture.started_at+b,
            speech_ended_at=capture.started_at+a+(intervals[-1][1] if intervals else 0.),
            voiced_intervals=intervals,parent_capture_id=capture.parent_capture_id or capture.capture_id,
            source_offset=capture.source_offset+a))
    return tuple(result)
