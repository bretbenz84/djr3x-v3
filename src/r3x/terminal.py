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
        elif event == "visual_opportunity":
            message = "Rex noticed an arrival and is considering a greeting…"
        elif event == "transcript":
            message = f"Heard: {clean(row['original_text'])}"
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
        elif event == "proactive_invalidated":
            message = "Rex's proactive turn stopped: " + clean(row['reason']).replace('_', ' ')
        elif event == "audio_input_health":
            level = 'digital silence' if row['peak_dbfs'] is None else f"peak {row['peak_dbfs']:.0f} dBFS"
            message = (f"Microphone ({clean(row['device'])}): {level}; "
                       f"speech detected in {row['speech_frames']}/{row['frames']} frames.")
        elif event == 'capture_gap':
            message = ('Microphone timing gap; interrupted input cleared.' if row.get('utterance_discarded')
                       else 'Microphone timing gap; listening resumed.')
        elif event == 'openai_waiting':
            message = (f"OpenAI unavailable ({clean(row['reason'])}); retrying in {row['retry_in_seconds']} seconds. "
                       + ('Your transcribed speech is retained while Rex stays running.' if row['human_turn']
                          else 'Waiting to try this proactive turn again.'))
        elif event == 'openai_recovered':
            message = 'OpenAI is responding again.'
        elif event == 'transcript_waiting':
            message = 'Transcribed speech is waiting for the conversation worker.'
        elif event == 'input_diagnostic_saved':
            message = 'Raw microphone diagnostic saved: ' + clean(row['path'])
        elif event == "face_learning_saved":
            message = f"Face samples saved ({row['samples']} clear views)."
        elif event == "voice_learning_saved":
            message = f"Voice samples saved ({row['samples']} compatible turns)."
        elif event == "voice_learning_progress":
            message = (f"Voice learning pending: {row['qualifying_turns']}/{row['required_turns']} turns, "
                       f"{row['voiced_seconds']:.1f}/{row['required_voiced_seconds']:g} usable speech seconds. "
                       "Voice profile not saved yet.")
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
