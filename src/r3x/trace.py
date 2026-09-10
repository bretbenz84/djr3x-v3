"""Local session evidence. Callers supply only sanitized errors and explicit data."""
import json
from pathlib import Path
import threading
import time
from typing import Callable


class Trace:
    def __init__(self, session_id: str, path: Path | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.session_id = session_id
        self.clock = clock
        self.lock = threading.Lock()
        self.handle = path.open("x", encoding="utf-8") if path else None

    def emit(self, event: str, **data) -> None:
        record = {"session_id": self.session_id, "at": self.clock(), "event": event, **data}
        with self.lock:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            if self.handle:
                self.handle.write(line + "\n")
                self.handle.flush()
            print(line, flush=True)

    def close(self) -> None:
        with self.lock:
            if self.handle:
                self.handle.close()
                self.handle = None


def report(path: Path) -> dict:
    """Missing timings remain missing; failed turns remain in counts."""
    from collections import Counter
    import math
    import statistics
    events = Counter()
    outcomes = Counter()
    latencies: dict[str, list[float]] = {}
    calls = 0
    for line in path.read_text().splitlines():
        row = json.loads(line)
        events[row["event"]] += 1
        if row["event"] == "model_request":
            calls += 1
        if row["event"] == "transcript":
            for key in ("asr_seconds", "queue_seconds"):
                if key in row:
                    latencies.setdefault(key, []).append(row[key])
        if row["event"] == "turn_done":
            outcomes[row["status"]] += 1
            for key, value in row.get("timings", {}).items():
                if value is not None:
                    latencies.setdefault(key, []).append(value)
    def summary(values):
        ordered = sorted(values)
        return {"n": len(ordered), "median": statistics.median(ordered),
                "p95": ordered[max(0, math.ceil(len(ordered)*.95)-1)]}
    return {"events": dict(events), "turn_outcomes": dict(outcomes), "model_calls": calls,
            "observed_seconds": {k: summary(v) for k, v in latencies.items()},
            "speaker_accuracy": None, "group_acceptance": False}
