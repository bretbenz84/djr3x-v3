"""Small live console view; the JSON trace remains the complete evidence record."""


def clean(value):
    # Transcripts/provider text must not inject terminal controls or extra rows.
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())


class LiveConsole:
    def __call__(self, row):
        event = row["event"]
        message = None
        if event == "starting":
            message = f"Starting Rex… Detailed log: {row['log_path']}"
        elif event == "listening":
            message = "Listening — speak normally. Enter stops a reply; quit + Enter exits."
        elif event == "transcript":
            message = f"You: {clean(row['original_text'])}"
        elif event == "model_request" and row.get("primary_calls"):
            message = "Rex is thinking…"
        elif event == "speech_generated":
            message = f"Rex (preparing speech): {clean(row['text'])}"
        elif event == "playback_started":
            message = "Speaking…"
        elif event == "delivery":
            if row["status"] == "suppressed":
                return
            if row["status"] == "failed":
                error = row.get("error") or "Unknown failure"
                if error == "ElevenLabsConnectTimeout":
                    detail = "Could not connect to ElevenLabs before the timeout; speech audio was not received."
                else:
                    detail = f"Speech failed: {clean(error)}."
                message = f"ERROR: {detail}"
                if row.get("completed_text"):
                    message += f" Delivered before failure: {clean(row['completed_text'])}"
            elif row["status"] == "interrupted":
                message = "Reply stopped."
            else:
                message = "Reply played."
            if row.get("underruns"):
                message += f" Playback underruns: {row['underruns']}."
        elif event == "turn_done":
            message = "Ready for the next turn."
        elif event == "reply_yielded":
            message = "Still listening — holding the reply while you continue."
        elif event == "reply_suppressed":
            message = "Rex stayed quiet: " + clean(row["reason"]).replace("_", " ")
        elif event == "voice_learning_saved":
            message = f"Voice samples saved ({row['samples']} compatible turns)."
        elif event == "tool_result":
            message = f"Action {clean(row.get('name', ''))}: {clean(row.get('outcome', ''))}"
        elif event == "cancel":
            message = "Stopping current reply…"
        elif event == "shutdown":
            message = "Rex stopped."
            failures = [r for r in row.get("report", {}).get("results", []) if r.get("outcome") == "failed"]
            if failures:
                message += " Cleanup error; see detailed log."
        elif event.endswith("_failed") or event in {"worker_stuck", "input_overflow", "capture_gap"}:
            details = row.get("reason") or row.get("error_type") or row.get("capability") or "See detailed log."
            message = f"ERROR: {event.replace('_', ' ')} — {clean(details)}"
        elif event in {"turn_rejected", "transcript_rejected"}:
            message = f"Input skipped: {clean(row.get('reason', 'untrusted transcript'))}"
        if message:
            print(message, flush=True)
