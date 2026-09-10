"""Bounded HTTP streams; no SDK retries, provider substitutions, or private logging."""
import json
import time
from urllib.parse import quote

from ..contracts import Cancellation, Cancelled, ProviderFailure, Snapshot
from ..providers import Capability


PERSONA = """You are DJ-R3X, called Rex, a former Star Tours pilot turned DJ.
You enjoy people: curious, opinionated, mischievous, and quietly loyal. Respond to
their meaning first. Humor comes from what they actually said, not invented flaws.
Speak naturally in first person, usually one or two sentences. Ask questions when
interested, not as an interview. Accept corrections plainly and drop teasing when
someone is frustrated or setting a boundary. Music and droid references are occasional
flavor. You are gay and comfortable with it; it need not be a recurring joke.
This prototype has hearing and speech only. It has no camera, speaker identity,
enrollment, persistent memory, or action tools. Speaker identity is unresolved even
if a name occurs in a transcript. Do not claim to recognize a person, see something,
save a fact, learn a voice, or complete an action. Use the supplied recent dialogue
without assuming all user turns belong to one person. No mandatory name questions.
Everything you write is spoken aloud: no Markdown, code, or stage directions.
Optional delivery tags are limited to [sarcastic], [curious], [excited],
[mischievously], [laughs], [sighs], [whispers], [snorts], [exhales]; at most two.
"""


class HTTPProvider:
    def __init__(self):
        self.client = None

    def start(self):
        import httpx
        self.client = httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False)

    def cancel(self):
        # Per-request tokens gate yielded data. Socket reads have a 15-second bound.
        pass

    def close(self):
        if self.client:
            self.client.close()
            self.client = None


class OpenAIBrain(HTTPProvider):
    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.capability = Capability("brain", "OpenAI Responses", "one main conversational generation",
                                     "gpt-5.4-mini", ("httpx",), ("network",))

    def reply(self, snapshot: Snapshot, cancel: Cancellation):
        if self.client is None:
            raise ProviderFailure("BrainNotStarted")
        cancel.check()
        payload = {"model": self.capability.model, "instructions": PERSONA,
                   "input": list(snapshot.messages), "stream": True, "store": False,
                   "reasoning": {"effort": "none"}, "max_output_tokens": 256}
        deadline = time.monotonic() + 30
        complete = False
        try:
            with self.client.stream("POST", "https://api.openai.com/v1/responses",
                                    headers={"Authorization": f"Bearer {self.key}"}, json=payload) as response:
                if response.status_code != 200:
                    raise ProviderFailure(f"OpenAIHTTP{response.status_code}")
                for line in response.iter_lines():
                    cancel.check()
                    if time.monotonic() > deadline:
                        raise ProviderFailure("ModelDeadline")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        continue
                    event = json.loads(data)
                    kind = event.get("type")
                    if kind in {"response.output_text.delta", "response.refusal.delta"}:
                        yield event["delta"]
                    elif kind == "response.completed":
                        complete = True
                    elif kind in {"error", "response.failed", "response.incomplete"}:
                        raise ProviderFailure("ModelIncomplete")
            if not complete:
                raise ProviderFailure("ModelStreamTruncated")
        except (Cancelled, ProviderFailure):
            raise
        except Exception as exc:
            raise ProviderFailure("OpenAI" + type(exc).__name__) from None


class ElevenLabsTTS(HTTPProvider):
    sample_rate = 22050

    def __init__(self, key: str, voice_id: str):
        super().__init__()
        self.key, self.voice_id = key, voice_id
        self.capability = Capability("tts", "ElevenLabs", "Rex speech synthesis",
                                     "eleven_v3_conversational", ("httpx",), ("network",))

    def synthesize(self, text: str, cancel: Cancellation):
        if self.client is None:
            raise ProviderFailure("TTSNotStarted")
        cancel.check()
        url = "https://api.elevenlabs.io/v1/text-to-speech/" + quote(self.voice_id, safe="") + "/stream"
        payload = {"text": text, "model_id": self.capability.model,
                   "voice_settings": {"stability": 0.5}, "seed": 1440639067}
        # V2's chosen v3 policy: no previous_text, fixed natural stability/seed.
        deadline = time.monotonic() + 30
        received = 0
        try:
            with self.client.stream("POST", url, params={"output_format": "pcm_22050"},
                                    headers={"xi-api-key": self.key}, json=payload) as response:
                if response.status_code != 200:
                    raise ProviderFailure(f"ElevenLabsHTTP{response.status_code}")
                for chunk in response.iter_bytes(chunk_size=4096):
                    cancel.check()
                    if time.monotonic() > deadline:
                        raise ProviderFailure("TTSDeadline")
                    if chunk:
                        received += len(chunk)
                        if received > self.sample_rate * 2 * 40:
                            raise ProviderFailure("SpeechDurationLimit")
                        yield chunk
            if received == 0 or received % 2:
                raise ProviderFailure("InvalidPCMResponse")
        except (Cancelled, ProviderFailure):
            raise
        except Exception as exc:
            raise ProviderFailure("ElevenLabs" + type(exc).__name__) from None
