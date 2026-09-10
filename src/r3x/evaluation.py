"""Frozen labels score predictions after inference. Abstention is never accuracy."""
from collections import defaultdict


class Evaluation:
    def __init__(self,enrolled_ids):
        self.enrolled = set(enrolled_ids)
        self.rows = []
        self.missing = []

    def add(self,clip,capture,attribution,voiced,offset):
        start,end = capture.started_at-offset,capture.speech_ended_at-offset
        segments = [s for s in clip["segments"] if min(s["end"],end)-max(s["start"],start)>.05]
        people = sorted({p for s in segments for p in s["speakers"]})
        mixed = len(people)>1 or clip["overlap"]
        expected = people[0] if len(people)==1 and not mixed else None
        prediction = attribution.person_id
        known = expected in self.enrolled if expected else False
        labeled_seconds = sum(max(0.,min(s["end"],end)-max(s["start"],start)) for s in segments)
        clean = bool(expected and known and clip["resolvable"] and not mixed and labeled_seconds>=2.)
        self.rows.append({"clip":clip["id"],"session":clip["session"],"device":clip["device"],
            "condition":clip["condition"],"expected":expected,"expected_speakers":people,
            "predicted":prediction,"track":attribution.track_id,"reason":attribution.reason,
            "resolvable":clip["resolvable"],"mixed":mixed,"known":known,"clean":clean,
            "voiced_seconds":voiced,"labeled_speech_seconds":labeled_seconds,"duration":max(0.,end-start),"named":prediction is not None,
            "correct":bool(prediction and expected==prediction and not mixed),
            "false_known":bool(prediction and (not known or mixed)),"short":labeled_seconds<2.})

    @staticmethod
    def metrics(rows):
        total = len(rows)
        named = sum(r["named"] for r in rows)
        correct = sum(r["correct"] for r in rows)
        seconds = sum(r["duration"] for r in rows)
        return {"captures":total,"named":named,"correct_named":correct,"wrong_named":named-correct,
            "abstentions":total-named,"named_precision":correct/named if named else None,
            "named_coverage":named/total if total else None,
            "false_known":sum(r["false_known"] for r in rows),
            "capture_seconds":seconds,
            "wrong_named_seconds":sum(r["duration"] for r in rows if r["named"] and not r["correct"]),
            "abstained_seconds":sum(r["duration"] for r in rows if not r["named"])}

    def report(self):
        groups = {}
        for key in ("expected","device","condition"):
            by = defaultdict(list)
            for row in self.rows:
                by[str(row[key])].append(row)
            groups[key] = {k:self.metrics(v) for k,v in by.items()}
        tracks = defaultdict(set)
        merged = defaultdict(set)
        for row in self.rows:
            if row["track"] and row["expected"]:
                tracks[(row["session"],row["expected"])].add(row["track"])
                merged[(row["session"],row["track"])].add(row["expected"])
        clean = self.metrics([r for r in self.rows if r["clean"]])
        return {"overall":self.metrics(self.rows),"clean_enrolled":clean,
            "short":self.metrics([r for r in self.rows if r["short"]]),
            "mixed":self.metrics([r for r in self.rows if r["mixed"]]),
            "unknown":self.metrics([r for r in self.rows if not r["known"] and not r["mixed"]]),
            "unresolvable":self.metrics([r for r in self.rows if not r["resolvable"]]),
            "by":groups,"extra_tracks_per_person_session":sum(max(0,len(v)-1) for v in tracks.values()),
            "merged_tracks":sum(len(v)>1 for v in merged.values()),"missing_or_failed_clips":self.missing,
            "proposed_clean_gate_passed":bool(clean["captures"]>=100 and clean["named_precision"] is not None and
                clean["named_precision"]>=.95 and clean["named_coverage"]>=.85 and not self.missing),
            "group_acceptance":False,"rows":self.rows,
            "limits":["Capture-duration metrics are not frame-level diarization error rates",
                      "Whole-buffer abstention diagnoses mixed captures; switch timestamps are not claimed",
                      "Live sessions and independently labeled unknown/overlap cohorts remain required"]}
