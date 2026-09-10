from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from r3x.terminal import LiveConsole
from r3x.trace import Trace, report


class TerminalTests(unittest.TestCase):
    def test_live_view_keeps_conversation_and_failure_while_file_retains_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            console = io.StringIO()
            with redirect_stdout(console):
                trace = Trace("synthetic", path, display=LiveConsole())
                trace.emit("camera_frame", faces=1, processing_seconds=.02)
                trace.emit("resources", peak_rss_bytes_macos=100)
                trace.emit("transcript", original_text="Are you there?")
                trace.emit("speech_generated", text="I am here.")
                trace.emit("delivery", turn_id="t", status="failed", error="ElevenLabsConnectTimeout")
                trace.close()
            text = console.getvalue()
            self.assertIn("You: Are you there?", text)
            self.assertIn("Rex (preparing speech): I am here.", text)
            self.assertIn("Could not connect to ElevenLabs", text)
            self.assertNotIn("Reply played", text)
            self.assertNotIn("camera_frame", text)
            self.assertNotIn("resources", text)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 5)
            self.assertEqual(report(path)["failures"][0]["error"], "ElevenLabsConnectTimeout")

    def test_generated_text_cannot_inject_console_rows_or_claim_delivery(self):
        console = io.StringIO()
        with redirect_stdout(console):
            display = LiveConsole()
            display({"event": "speech_generated", "text": "hello\n\x1b[2Jworld"})
        self.assertEqual(len(console.getvalue().splitlines()), 1)
        self.assertNotIn("\x1b", console.getvalue())
        self.assertNotIn("Reply played", console.getvalue())
        with redirect_stdout(console):
            display({"event": "playback_started"})
            display({"event": "delivery", "status": "completed", "underruns": 2})
        self.assertIn("Speaking…", console.getvalue())
        self.assertIn("Reply played. Playback underruns: 2.", console.getvalue())
