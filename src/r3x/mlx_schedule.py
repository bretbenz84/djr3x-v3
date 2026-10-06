"""One process-wide MLX turn-taking lock shared by every local Metal model.

Separate Python threads do not make MLX workloads independent: V2 (2026-07-19) saw
native crashes when a transcription and a Breeze generation evaluated concurrently.
Transcription holds the lock for one utterance; voice cloning holds it per generated
chunk, so a transcription interleaves between chunks instead of colliding with them.
"""
import threading

MLX_LOCK = threading.RLock()
