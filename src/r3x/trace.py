"""Local session evidence. Callers supply only sanitized errors and explicit data."""
import json
from pathlib import Path
import threading
import time
from typing import Callable


class Trace:
    def __init__(self, session_id: str, path: Path | None = None,
                 clock: Callable[[], float] = time.monotonic, display=None):
        self.session_id = session_id
        self.clock = clock
        self.display = display
        self.lock = threading.Lock()
        self.handle = path.open("x", encoding="utf-8") if path else None

    def emit(self, event: str, **data) -> None:
        record = {"session_id": self.session_id, "at": self.clock(), "event": event, **data}
        with self.lock:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            if self.handle:
                self.handle.write(line + "\n")
                self.handle.flush()
            if self.display is None:
                print(line, flush=True)
            else:
                self.display(record)

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
    proactive = Counter()
    intent_transitions, intent_rejections = Counter(), Counter()
    purposes = Counter()
    latencies: dict[str, list[float]] = {}
    calls = 0
    images,image_bytes = 0,0
    observation_receipts = Counter()
    token_usage = Counter()
    primary,continuations,underruns = 0,0,0
    directions = Counter()
    evidence,models,failures = set(),set(),[]
    rss = []
    for number,line in enumerate(path.read_text().splitlines(),1):
        try:
            row = json.loads(line)
            if not isinstance(row,dict) or not isinstance(row.get("event"),str):
                raise ValueError("InvalidTraceRow")
        except ValueError:
            failures.append({"event":"trace_parse_failed","line":number})
            continue
        events[row["event"]] += 1
        if row['event']=='conversation_intent_transition':intent_transitions[row['reason']] += 1
        if row['event']=='conversation_intent_rejected':intent_rejections[row['reason']] += 1
        if row['event'] == 'direction_evidence':
            directions[row['status']] += 1
        if row["event"] == "model_request":
            purposes[row.get('purpose','conversation')] += 1
            calls += 1
            images += row.get("image_count",0)
            image_bytes += row.get("image_bytes",0)
            primary += row.get("primary_calls",1)
            continuations += int(row.get("purpose")=="tool_result_continuation")
        if row["event"]=="model_usage":
            for key in ("input_tokens","output_tokens","total_tokens"):token_usage[key]+=row.get(key,0)
        if row["event"]=="observation_delivered":observation_receipts[row["kind"]]+=1
        if row["event"]=="observation_refresh" and row.get("outcome")=="failed":
            failures.append({k:row[k] for k in ("event","source","error_type") if k in row})
        if row.get("evidence"):
            evidence.add(row["evidence"])
        models.update(m for m in row.get("models",[]) if m)
        if row["event"] in {"provider_failed","turn_failed","worker_stuck","state_finalize_failed","replay_clip_failed","startup_failed"}:
            failures.append({k:row[k] for k in ("event","turn_id","capability","reason","error_type") if k in row})
        if row["event"]=="delivery":
            underruns += row.get("underruns",0)
            if row.get("status") == "failed":
                failures.append({k: row[k] for k in ("event", "turn_id", "error") if k in row})
        if row["event"]=="resources":
            rss.append(row["peak_rss_bytes_macos"])
        if row['event']=='proactive_outcome':
            proactive[(row['kind'],row['status'],row['medium'],row['heard_complete_phrases'])] += 1
        for event,key in (("speaker_analysis","speaker_seconds"),("camera_frame","processing_seconds"),
                          ("tts_first_pcm","tts_first_pcm_seconds"),("playback_started","playback_queue_seconds"),
                          ("direction_sample","read_seconds")):
            if row["event"]==event and row.get(key) is not None:
                latencies.setdefault(key,[]).append(row[key])
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
            "direction_evidence": dict(directions),
            "conversation_intentions": {"transitions":dict(intent_transitions), "rejections":dict(intent_rejections)},
            "model_calls_by_purpose":dict(purposes),"scene_image_inputs":images,"scene_image_bytes":image_bytes,
            "observation_deliveries":dict(observation_receipts),"reported_model_tokens":dict(token_usage),
            "proactive_outcomes":[{'kind':key[0],'status':key[1],'medium':key[2],
                                    'heard_complete_phrases':key[3],'count':count}
                                   for key,count in proactive.items()],
            "primary_calls":primary,"tool_result_continuations":continuations,"evidence":sorted(evidence),
            "models":sorted(models),"failures":failures,"playback_underruns":underruns if events["playback_started"] else None,
            "peak_rss_bytes_macos":max(rss) if rss else None,
            "observed_seconds": {k: summary(v) for k, v in latencies.items()},
            "speaker_accuracy": None, "group_acceptance": False}
