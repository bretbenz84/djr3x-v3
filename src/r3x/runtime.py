"""Live orchestration: bounded capture/ASR queues feeding the shared Session."""
from collections import deque
import re
from queue import Queue, Empty, Full
from dataclasses import replace
import sys
import threading
import time
from uuid import uuid4

from .capture import Endpoint
from .contracts import Cancellation, Cancelled, Capture, ProviderFailure
from .session import is_collision_warning, is_quiet_command, is_stop_command, stop_heard_over

# Voiced time within one utterance before it yields an unheard reply. Live September 28:
# every phantom "Oh."/"Okay." noise capture had <= 0.48 s voiced; real speech >= 0.64 s.
YIELD_VOICED_SECONDS = .5
# Voiced time before the head-turn reflex looks up the voice's direction. The
# direction must still be clear (3+ strong samples, one dominant beam), which the
# single-frame noises heard as "Oh." rarely were.
REFLEX_VOICED_SECONDS = .3
# A sound that starts while Rex's own servos move is suspected self-noise until it lasts
# like speech. Live October 3 (30ec1b04): 6 of 10 idle moves were heard as "Okay."/"Oh."
# (0.03-0.42 s voiced); each froze idle motion, several cancelled his own beats and two
# cost a reply call. Real utterances in that run had at least 0.74 s voiced. A suspected
# sound is still captured and transcribed (nothing is discarded); it just does not stop
# his movement or his initiative, and its transcript needs clearer recognition to be trusted.
CONFIRM_RUN_SECONDS = .25      # Continuous voiced time that confirms speech during motion.
CONFIRM_RECENT_SECONDS = .4    # Or this much voiced time within the last second.
SUSPECT_OPEN_SECONDS = 3.5     # A suspected capture open this long gets a still moment to end.
SELF_NOISE_LOGPROB = -.1       # Trust floor for a suspected capture (the usual floor is lower).
# The exact spoken shutdown, with his name as recognition often hears it (October 22, 0421a222:
# "Red shut down." was taken as words for someone else and he stayed on).
SHUTDOWN_COMMANDS = frozenset({'shut down', 'shutdown', 'shut down rex'} | {
    f'{name}{sep} shut down' for name in ('rex', 'red', 'wrecks', 'wrex', 'rick', 'rx') for sep in ('', ',')})
# Owner, October 19 (b4ce6464): a shouted "stop" halted the wheels about 1 s after the word
# ended (0.5 s waiting for the end of the sentence, 0.3 s transcription, queueing). While the
# wheels run, a short burst followed by a brief pause is transcribed at once, beside the
# normal capture; a stop word or collision warning halts the drive without waiting for the
# endpoint. Nothing else is acted on early: the full capture still becomes the turn.
STOP_PEEK_SILENCE = .2         # Pause after a short burst before it is checked for "stop".
STOP_PEEK_MAX_VOICED = 1.5     # Longer speech is a sentence, not a stop: normal endpointing.
# His own line said a stop word this recently: a stop-word detection may be his voice heard back.
STOP_WORD_OWN_SECONDS = 1.5
# After "Hey Rex" cuts his line (owner, October 25), sound from this long after the word is the
# person's, for this long or until he speaks again; the cut itself takes one playback callback.
WAKE_CUT_SECONDS = .1
WAKE_LISTEN_SECONDS = 3.
WAKE_SPEAKING_AGAIN_SECONDS = .5   # Playing after this long is a new line of his.
# While the wheels run the board turns everything down (October 25: the processed channel's quietest
# tenth of moving seconds was -55 to -65 dBFS, a still room -47 to -52), and a "stop" from across the
# room rarely lifts the speech detector to its usual .5. A sound this likely to be speech is taken.
DRIVE_SPEECH_PROBABILITY = .3
# How long the idle tick waits for a just-started goal plan before returning to listening.
# Hosted planning takes seconds and finishes in the background; this bounds the pause.
PLANNING_GLANCE_SECONDS = .25
# An unrecognized face as the only face in view this soon after someone known was recognized
# (face or voice) is them from another angle, not a newcomer to ask for a name (October 22).
KNOWN_FACE_TURN_SECONDS = 60.
GREETING_FIRST_SECONDS = 20.   # At startup, a face in view waits this long for its greeting before Rex plans alone.
# Sounds that stop a hello before anyone is known to have spoken; a thrown-away one earns a retry.
NOISE_CUTOFFS = frozenset({'human_speech','capture_queued','turn_queued','recognition_busy'})
SHUTDOWN_WORKER_SECONDS = 2.   # How long shutdown lets signalled workers finish before leaving them.
WAITING_TURNS = 8   # Transcripts kept while the conversation is busy (a long drive), beyond the queue.

class VoiceRuntime:
    def __init__(self, mic, vad, asr, session, trace, silence=0.7, perception=None, recorder=None, *, lull_seconds=8.0, observations=None, scene_observer=None, robot=None, drive=None, keywords=None, turn=None):
        self.mic, self.vad, self.asr, self.session, self.trace = mic, vad, asr, session, trace
        self.turn = turn             # adapters.turn.SmartTurn: a short pause can end a finished-sounding turn.
        self.keywords = keywords     # adapters.keywords.KeywordSpotter, fed every frame.
        self._spotting = None        # When the current drive's stop listening began, or None.
        self._spot_peak = 0.
        self._wake_at = None         # When a wake word last cut his line: later sound is the person's.
        self._wake_listening = None  # [began, {word: peak score}] while he speaks.
        self.endpoint = Endpoint(session.session_id, silence, self.turn_check if turn else None)
        self.silence = silence       # The usual end-of-speech pause (a Jeopardy clue allows a longer one).
        self._music_off_for = None   # The Jeopardy answer whose think music a voice stopped (not restarted).
        self._music_for = None       # The answer the think music last played for (it resumes there).
        self.perception, self.recorder = perception, recorder
        self.captures = Queue(maxsize=2)
        # Captures and turns on their way that may be words for Rex: confirmed speech, and a
        # brief sound that recognition then trusted. October 18 (6aaf22aa): 11 of 13 beats cut
        # off in flight were cut off by his own motors heard as "Okay." or "Oh.", so an
        # unconfirmed sound no longer interrupts a beat; its transcript decides.
        self.words_pending = set()
        self.words_lock = threading.Lock()
        self.turns = Queue(maxsize=3)
        # Transcripts waiting for room in turns. October 16 (3af18a12): the speech-recognition
        # worker blocked on a full turns queue during a two-minute drive, so every later capture,
        # "stop" included, was dropped untranscribed. It now keeps listening while these wait.
        self.waiting_turns = deque()
        self._waiting_reported = False
        self.stop = threading.Event()
        self.asr_cancel = Cancellation()
        self.workers = []
        self.cancel_before = float("-inf")
        self.control_lock = threading.Lock()
        self.asr_busy = threading.Event()
        from .proactive import Arrivals
        self.arrivals = Arrivals()
        self.camera = getattr(perception,'camera',None)
        self.started_at = self.trace.clock()
        self.audio_seen_at = float('-inf')
        self.lull_seconds = lull_seconds
        self.observations,self.scene_observer = observations,scene_observer
        self.robot = robot
        self.drive = drive
        self._initiative_gate = None
        self.speech_confirmed = False   # The forming capture has lasted like speech, or began in stillness.
        self.sentry = None   # sentry_guard.SentryGuard: while it is on, what Rex hears goes to it first.
        session.on_shutdown = self.conversation_shutdown

    def conversation_shutdown(self, turn_id):
        """The same exit as the spoken "shut down" fast path, chosen by the conversation owner."""
        self.request_shutdown('conversation', turn_id=turn_id)

    def request_shutdown(self, source, **detail):
        """Normal exit from any thread (conversation, the dashboard's Shut Down button)."""
        if self.stop.is_set():
            return
        self.trace.emit('shutdown_requested', source=source, **detail)
        self.stop.set()
        self.cancel()

    def cancel(self, quiet=False):
        if self.robot:
            self.robot.idle.stop()
            self.robot.reflex.stop()
            self.robot.owner.release_microphone_hold()
        # Shutdown goes ahead even if a stuck worker holds the control lock.
        from .session import SHUTDOWN_LOCK_SECONDS
        locked = self.control_lock.acquire(timeout=SHUTDOWN_LOCK_SECONDS if self.stop.is_set() else -1)
        try:
            self.cancel_before = self.trace.clock()
            self.session.cancel('shutdown' if self.stop.is_set() else 'explicit_stop',
                                quiet=quiet and not self.stop.is_set())
            self.asr_cancel.event.set()
            with self.words_lock: self.words_pending.clear()
        finally:
            if locked: self.control_lock.release()
        self.trace.emit("input_invalidated", captured_before=self.cancel_before)

    def counting_down(self):
        """Sentry mode's countdown: his own numbers are heard too, and only a call-off word counts."""
        sentry = getattr(self, 'sentry', None)
        return sentry is not None and sentry.echo_allowed()

    def guarding(self):
        sentry = getattr(self, 'sentry', None)
        return sentry is not None and sentry.active

    def enqueue(self, queue, item, kind):
        if kind=='captures' and item[0].echo_risk and not self.counting_down():
            # Echo transitions must not masquerade as queued human work in the
            # proactive guard. Evaluation recording has already kept this PCM.
            self.trace.emit('capture_rejected',capture_id=item[0].capture_id,reason='playback_echo_risk')
            return
        if kind=='turns':
            # Do not discard an already transcribed utterance during a slow request or a long
            # drive, and never stop transcribing for it: a stop must still be heard.
            self.waiting_turns.append(item)
            if len(self.waiting_turns) > WAITING_TURNS:
                # Drop the oldest unclear sound first, then the oldest words.
                drop = next((w for w in self.waiting_turns if not w[1].trusted), self.waiting_turns[0])
                self.waiting_turns.remove(drop)
                self.trace.emit('input_overflow', queue='waiting_turns', policy='drop_oldest_untrusted_first',
                                capture_id=drop[0].capture_id)
            self.flush_turns()
            return
        try:
            queue.put_nowait(item)
            if kind=='captures' and not item[0].self_noise_risk:
                self.words_arriving(item[0].capture_id)
        except Full:
            self.trace.emit("input_overflow", queue=kind, policy="reject_newest")

    def _stop_peek(self, burst, revision):
        """Transcribe sound heard while the wheels run; halt at once on a stop word.

        A short burst is checked after a brief pause, and (owner, October 24: he talks while he
        rolls) so is anything captured while he was speaking, which the conversation never hears:
        his own line's words are set aside first."""
        said_between = getattr(self.session.output, 'said_between', None)
        own = said_between(burst.started_at, burst.ended_at) if said_between else ''
        def run():
            began = self.trace.clock()
            try:
                transcript = self.asr.transcribe(burst, Cancellation())
            except Exception as exc:
                self.trace.emit('stop_peek', status='failed', error_type=type(exc).__name__)
                return
            halt = stop_heard_over(transcript.text, own)
            # October 25 (tools/check_stop_hearing.py): every stop word the recognizer made out of
            # drive noise was an unconfident "Whoa!"; the real stops were mostly confident.
            unsure = halt and not transcript.trusted and set(re.findall(r"[a-z]+", transcript.text.lower())) <= {'whoa', 'oh'}
            halt = halt and not unsure
            acted = (halt and revision == self.session.revision and not self.stop.is_set()
                     and self.drive.moving.is_set())
            if acted:
                self.drive.cancel()   # Wheels first; the conversation's own stop follows.
                self.cancel(quiet=is_quiet_command(transcript.text))
            self.trace.emit('stop_peek', status='halted' if acted else 'stop_word_late' if halt else
                            'unsure_whoa' if unsure else 'not_a_stop',
                            text=transcript.text, avg_logprob=transcript.avg_logprob, over_own_speech=bool(own),
                            echo_risk=burst.echo_risk, asr_seconds=round(self.trace.clock() - began, 3),
                            after_speech_seconds=round(self.trace.clock() - burst.speech_ended_at, 3))
        threading.Thread(target=run, name='r3x-stop-peek', daemon=True).start()

    def _listen_for_keywords(self, frame):
        """Spoken keywords heard in the sound itself (adapters/keywords.py), beside the speech
        detector and recognizer. "Stop" halts the wheels while they run. "Hey Rex" or "Hey DJ Rex"
        said over him cuts his line so the person can talk (owner, October 25); while the wheels
        run it waits, since only a stop or a warning halts a drive. His own line saying the word
        never counts; sentry mode decides for itself."""
        from .adapters.keywords import WAKE_WORDS
        try:
            # October 25: on channel 0 the board ducks a voice about 20 dB while he plays (V2's Flex
            # measurements), and "Hey Rex" shouted over him never fired; channel 1 is not ducked.
            fired = self.keywords.feed(frame.beam_pcm or frame.pcm)
        except Exception as exc:
            self.trace.emit('keywords_failed', error_type=type(exc).__name__)
            self.keywords = None
            return
        echo_risk = getattr(self.session.output, 'echo_risk', None)
        speaking = bool(echo_risk and echo_risk())
        scores = getattr(self.keywords, 'last_scores', None) or {}
        if speaking:
            if self._wake_listening is None:
                self._wake_listening = [frame.started_at, {}]
            peaks = self._wake_listening[1]
            for word in (*WAKE_WORDS, 'stop'):
                peaks[word] = max(peaks.get(word, 0.), scores.get(word, 0.))
        elif self._wake_listening is not None:
            # Evidence for live checks: how close a wake word came while he spoke.
            began, peaks = self._wake_listening
            self.trace.emit('wake_word_listened', seconds=round(frame.ended_at - began, 2),
                            channel=1 if frame.beam_pcm is not None else 0,
                            peaks={w: round(v, 3) for w, v in sorted(peaks.items())})
            self._wake_listening = None
        moving = self.drive is not None and self.drive.moving.is_set()
        if moving:
            if self._spotting is None:
                self._spotting, self._spot_peak = frame.started_at, 0.
            self._spot_peak = max(self._spot_peak, (getattr(self.keywords, 'last_scores', None) or {}).get('stop', 0.))
        elif self._spotting is not None:
            # Evidence for the next live check: how close a stop came while he rolled.
            self.trace.emit('stop_word_listened', seconds=round(frame.ended_at - self._spotting, 2),
                            peak_score=round(self._spot_peak, 3))
            self._spotting = None
        if not fired or self.guarding() or self.stop.is_set():
            return
        said_between = getattr(self.session.output, 'said_between', None)
        own = (said_between(frame.ended_at - STOP_WORD_OWN_SECONDS, frame.ended_at) if said_between else '') or ''
        if 'stop' in fired and (moving or speaking):
            # Owner, October 25: like "Hey Rex", a "stop" said over his voice is heard on channel 1;
            # standing still it ends his speech exactly as a spoken stop does.
            if re.search(r"\bstop", own.lower()):   # "Stopped.", "I'll stop at the door."
                self.trace.emit('stop_word_heard', status='his_own_line', score=round(fired['stop'], 3))
            else:
                if moving:
                    self.drive.cancel()   # Wheels first; the conversation's own stop follows.
                self.cancel()
                self.trace.emit('stop_word_heard', status='halted' if moving else 'stopped_speech',
                                score=round(fired['stop'], 3), echo_risk=frame.echo_risk)
                return
        wake = next((w for w in fired if w in WAKE_WORDS), None)
        if wake is None:
            return
        if not speaking:   # Not over his voice: the words become an ordinary turn.
            self.trace.emit('wake_word_heard', word=wake, status='not_over_his_voice', score=round(fired[wake], 3))
            return
        status = ('driving' if moving else 'his_own_line' if re.search(r"\brex\b", own.lower()) else
                  'interrupted' if self.session.interrupt_speech(wake) else 'nothing_to_interrupt')
        self.trace.emit('wake_word_heard', word=wake, status=status, score=round(fired[wake], 3))
        if status == 'interrupted':
            self._wake_at = frame.ended_at   # What follows is theirs, not his echo.
            self.session.note_speech(frame.ended_at)
            if self.robot:
                try:
                    self.robot.heard()   # Show he is listening.
                except Exception as exc:
                    self.trace.emit('listening_cue_skipped', reason='failed:' + type(exc).__name__)

    def flush_turns(self):
        """Move waiting transcripts into the conversation queue, in order, as room appears."""
        while self.waiting_turns:
            try:
                self.turns.put_nowait(self.waiting_turns[0])
            except Full:
                if not self._waiting_reported:
                    self.trace.emit('transcript_waiting', reason='conversation_queue_full')
                    self._waiting_reported = True
                return
            self.waiting_turns.popleft()
        self._waiting_reported = False

    def words_arriving(self, capture_id):
        with self.words_lock: self.words_pending.add(capture_id)

    def words_settled(self, capture_id):
        with self.words_lock: self.words_pending.discard(capture_id)

    def turn_check(self, pcm):
        """Smart Turn on the capture so far, from the listening loop; None when it cannot judge.

        Not while the wheels run: that speech waits for the drive's end anyway (owner, October 16),
        and a short burst then gets its own stop check at the same 0.2 s, which an early end would skip."""
        if self.drive is not None and self.drive.moving.is_set():
            return None
        began = time.perf_counter()
        try:
            probability = self.turn.finished(pcm)
        except Exception as exc:
            self.trace.emit("provider_failed", capability="turn_detection", error_type=type(exc).__name__)
            return None
        self.trace.emit("turn_check", probability=round(probability, 3), seconds=round(time.perf_counter()-began, 4),
                        audio_seconds=round(len(pcm)/32000, 2))
        return probability

    def words_on_the_way(self):
        """Whether queued or half-recognized audio may still be words for Rex."""
        with self.words_lock: return bool(self.words_pending)

    def _asr_loop(self):
        while not self.stop.is_set():
            self.flush_turns()
            try:
                with self.control_lock:
                    capture, revision = self.captures.get_nowait()
                    self.asr_busy.set()
            except Empty:
                self.stop.wait(.05)
                continue
            if revision != self.session.revision or capture.started_at <= self.cancel_before:
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="cancelled")
                self.words_settled(capture.capture_id)
                self.asr_busy.clear()
                continue
            if capture.echo_risk and not self.counting_down():
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="playback_echo_risk")
                self.words_settled(capture.capture_id)
                self.asr_busy.clear()
                continue
            with self.control_lock:
                if revision != self.session.revision or self.stop.is_set():
                    self.words_settled(capture.capture_id)
                    self.asr_busy.clear()
                    continue
                self.asr_cancel = Cancellation()
            began = self.trace.clock()
            words = False   # A trusted turn under this capture's own id keeps it among the words pending.
            try:
                perceived_turns = self.perception.analyze_many(capture,self.asr_cancel) if self.perception else (None,)
                cued = False   # One listening cue per capture, however many turns it splits into.
                for perceived in perceived_turns:
                    part = perceived.capture if perceived else capture
                    transcript = perceived.transcript if perceived else self.asr.transcribe(part, self.asr_cancel)
                    if not perceived:
                        self.trace.emit("transcript", capture_id=part.capture_id, original_text=transcript.heard_as or transcript.text,
                            **({"corrected_text": transcript.text} if transcript.heard_as else {}),
                            avg_logprob=transcript.avg_logprob, trusted=transcript.trusted,
                            asr_seconds=self.trace.clock() - began, queue_seconds=began - part.ended_at)
                    if revision != self.session.revision or self.stop.is_set():
                        self.trace.emit("transcript_rejected", capture_id=part.capture_id, reason="stale")
                        break
                    command = transcript.text.strip().lower().rstrip('.!?')
                    # Sentry mode decides for itself: an intruder's "stop" or "shut down" changes nothing.
                    guarded = self.guarding()
                    shutdown = command in SHUTDOWN_COMMANDS
                    warning = (self.drive is not None and self.drive.moving.is_set()
                               and is_collision_warning(transcript.text))
                    if (capture.self_noise_risk and transcript.trusted and not shutdown and not warning
                            and not is_stop_command(transcript.text)
                            and (transcript.avg_logprob is None or transcript.avg_logprob < SELF_NOISE_LOGPROB)):
                        # Lower confidence, not a discard: the words stay in the trace and
                        # history evidence, but a brief sound made while his own servos
                        # moved does not become a turn unless it was recognized clearly.
                        transcript = replace(transcript, trusted=False)
                        if perceived:
                            perceived = replace(perceived, transcript=transcript)
                        self.trace.emit('transcript_distrusted', capture_id=part.capture_id,
                                        reason='brief_sound_during_own_motion', avg_logprob=transcript.avg_logprob)
                    if transcript.trusted and command in SHUTDOWN_COMMANDS and not guarded:
                        self.trace.emit('shutdown_requested',source='voice',capture_id=part.capture_id)
                        self.stop.set()
                        self.cancel()
                        break
                    if transcript.trusted and (is_stop_command(transcript.text) or warning) and not guarded:
                        # Bypasses a busy conversational generation.
                        if warning:
                            self.trace.emit('collision_warning_stop', capture_id=part.capture_id)
                        if is_quiet_command(transcript.text): self.cancel(quiet=True)
                        else: self.cancel()
                        break
                    else:
                        if self.robot and transcript.trusted and transcript.text.strip() and not cued and not guarded:
                            # Show he heard it while the reply is still being thought of.
                            cued = True
                            try:
                                outcome = self.robot.heard()
                            except Exception as exc:
                                outcome = 'failed:' + type(exc).__name__
                            if outcome != 'started':
                                self.trace.emit('listening_cue_skipped', capture_id=part.capture_id, reason=outcome)
                        if transcript.trusted:
                            # Words, whether or not the sound was confirmed as speech: a beat
                            # still being prepared yields to them (turn_queued).
                            self.words_arriving(part.capture_id)
                            words = words or part.capture_id == capture.capture_id
                        self.enqueue(self.turns, (part, transcript, revision, perceived), "turns")
            except Cancelled:
                self.trace.emit("asr_cancelled", capture_id=capture.capture_id)
            except Exception as exc:
                self.trace.emit("provider_failed", capability="asr", error_type=type(exc).__name__)
            finally:
                if not words: self.words_settled(capture.capture_id)
                self.asr_busy.clear()

    def _turn_loop(self):
        while not self.stop.is_set() and not self.session.closed:
            try:
                capture, transcript, revision, perceived = self.turns.get(timeout=.05)
            except Empty:
                if self.guarding():
                    self.sentry.tick(self.trace.clock())   # Instead of initiative: sentry mode watches.
                elif self.gaming():
                    self.game_tick()   # A game's clock instead of his own goals (owner, October 26).
                else:
                    self.endpoint.silence, self.endpoint.early = self.silence, True
                    self.proactive_tick()
                continue
            self.words_settled(capture.capture_id)
            if self.guarding() and self.sentry.hear(capture, transcript, perceived):
                continue
            delivery = self.session.process_turn(capture, transcript, revision,
                observation=perceived.voice if perceived else None, faces=perceived.faces if perceived else (),
                latest_faces=self.camera.latest if self.camera else None)
            if self.sentry is not None:
                self.sentry.after_reply(delivery)
        if self.session.closed:
            self.stop.set()

    def gaming(self):
        state = self.session.state
        return state is not None and state.jeopardy.active

    def speech_in_flight(self):
        """Words that may still become a turn: being spoken, waiting for recognition or for the conversation."""
        return bool(self.asr_busy.is_set() or not self.captures.empty() or not self.turns.empty()
                    or self.waiting_turns or (self.speech_confirmed and self.endpoint.frames))

    def game_tick(self):
        """Jeopardy's clock and music while a game is on (owner, October 26)."""
        game = self.session.state.jeopardy
        from .jeopardy import ANSWER_PAUSE_SECONDS
        # "What is... Paris?": a clue allows a thinking pause inside the response (V2: 1.1 s).
        self.endpoint.silence = max(self.silence, ANSWER_PAUSE_SECONDS) if game.answering() else self.silence
        self.endpoint.early = not game.answering()   # "What is..." must not end at a short pause.
        in_flight = self.speech_in_flight()
        self.session.game_tick(self.trace.clock(), in_flight)
        output = self.session.output
        bed = getattr(output, 'bed', None)
        if bed is None or not self.gaming():
            return
        attempt = game.attempt_id()
        # A turn being weighed does not stop it; only a voice, his own line or the end of the wait does.
        want = game.clue_waiting() and attempt != self._music_off_for
        if want and not bed.playing and not in_flight and not output.active.is_set():
            pcm = game.sound('theme')
            at = bed.position if attempt == self._music_for else 0   # After his line mid-clue it picks up.
            if pcm and bed.start(pcm, at):
                self._music_for = attempt
                self.trace.emit('jeopardy_music', status='playing', attempt_id=attempt, resumed=bool(at))
        elif not want and bed.playing:
            bed.stop(wait=False)
            self.trace.emit('jeopardy_music', status='stopped', attempt_id=attempt)

    def voice_over_music(self):
        """Someone is answering: the think music stops for them and stays off for this clue (the board turns
        a voice down while anything plays; V2 measured about 20 dB)."""
        bed = getattr(self.session.output, 'bed', None)
        if bed is not None and bed.playing:
            bed.stop(wait=False)
            self._music_off_for = self.session.state.jeopardy.attempt_id() if self.session.state else None
            self.trace.emit('jeopardy_music', status='stopped_for_voice', attempt_id=self._music_off_for)

    def observe_visual(self, now):
        if self.camera:
            self.arrivals.observe(self.camera.latest(now) if self.camera else (),now)
            self.session.check_opportunity()

    def initiative_gate(self, reason):
        """Report state changes, not every idle poll."""
        if reason != self._initiative_gate:
            self._initiative_gate = reason
            policy = self.session.state.initiative if self.session.state else None
            self.trace.emit('initiative_gate', reason=reason,
                            due_at=policy.agency.due_at if policy and policy.agency and policy.agency.due_at!=float('-inf') else None)

    def proactive_tick(self, _after_planning=False):
        """Rex's own initiative, in priority order (owner direction, October 2):
        people talking come first (they never reach this method's work), a person who
        arrives is greeted, and when nothing is happening Rex pursues his goal. Goals are
        planned in the background; a human turn neither waits for nor discards them."""
        if not self.session.state or self.session.state.frozen:
            self.initiative_gate('state_unavailable_or_frozen')
            return
        now = self.trace.clock()
        with self.control_lock:
            self.arrivals.observe(self.camera.latest(now) if self.camera else (), now)
            present = self.session.state.present
            objects = getattr(self.session.state, 'objects', None)
            present.saw_things(objects.snapshot() if objects else (), now)
            initiative = self.session.state.initiative
            agency = self.session.state.agency
            present.saw_faces(self.arrivals.faces, now)
            quiet_seconds = min(now-self.started_at, now-self.session.last_trusted_speech_at,
                                now-self.session.last_interaction_at)
            audience, audience_key = self.arrivals.audience_snapshot(now)
            with self.session._lock:
                intentions = self.session.state.conversation_intent
                if intentions.record:
                    from .memory_opportunities import candidates
                    memories = candidates(self.session.state.store, self.arrivals.faces,
                        self.session.state.calendar_today(), limit=128, include_delivered=True,
                        now=self.session.state.wall_clock())
                    intentions.refresh({'faces': self.arrivals.faces,
                        'participation': initiative.context(now)}, [r['claim_id'] for r in memories])
            gate = ('shutdown' if self.stop.is_set() or self.session.closed else
                    'startup' if now-self.started_at < 2 else
                    'audio_unavailable' if not 0 <= now-self.audio_seen_at <= .5 else
                    'human_work_pending' if self.asr_busy.is_set() or not self.captures.empty() or
                        not self.turns.empty() or (self.speech_confirmed and bool(self.endpoint.frames)) else
                    'unresolved_speech' if self.session._latest_speech > self.session.resolved_audio_at and
                        now-self.session._latest_speech < self.lull_seconds else
                    'explicit_stop_or_human_floor' if now < self.session.proactive_hold_until else
                    'quiet_requested' if initiative.quiet else
                    'human_floor' if initiative.floor_held(now) else
                    'agency_planner_unavailable' if self.session.agency_planner is None else None)
            if gate:
                self.initiative_gate(gate)
                return
            if quiet_seconds < 2:
                self.initiative_gate('conversation_active')
                return
            planning_only = False
            # People first: a settled new arrival is greeted before any goal work.
            event = self.arrivals.take(self.session.session_id, now, self.session._latest_speech,
                                      self.session.human_turns,
                                      known_just_now=present.recently_recognized(now, KNOWN_FACE_TURN_SECONDS))
            while self.arrivals.passed_over:
                self.trace.emit('arrival_skipped', track_id=self.arrivals.passed_over.pop(0),
                                reason='unrecognized_view_of_someone_here')
            if event is not None and event.person_id and not present.arriving(event.person_id, now):
                # Seen or heard within the last few minutes: their face was out of view or
                # unrecognized for a while, but this is the same visit, not an arrival to greet
                # again (October 5-13: 6 of 8 greetings went to someone seen under two minutes before).
                self.trace.emit('arrival_skipped', person_id=event.person_id, reason='same_visit')
                event = None
            if event is None and present.someone_around(now, self.started_at):
                # Then an animal coming into view: Max in someone's arms is worth a remark.
                event = present.pet_arrival(self.session.session_id, now, self.session._latest_speech, audience)
            # A person's words whose reply was dropped unheard (not for more speech) come next, before
            # his own goal (October 23: the marionette line went unanswered while a goal beat ran).
            if event is None and getattr(self.session, '_unanswered_turn', None) is None:
                if not present.someone_around(now, self.started_at):
                    self.initiative_gate('nobody_around')
                    return
                with self.session._lock:
                    agency.adopt_prepared(now)   # A plan written ahead follows the last beat at once.
                    from .hypotheticals import QUIET_SECONDS
                    if quiet_seconds >= QUIET_SECONDS and present.here(None, now):
                        # One "Do you ever think..." per quiet stretch (owner, October 23), for someone in view.
                        agency.long_quiet(now)
                if agency.needs_plan(now):
                    if self.session.planning_active():
                        self.initiative_gate('planning_goal')
                        return
                    if (agency.ending_reason(now) in {'startup', 'restart'} and now - self.started_at < GREETING_FIRST_SECONDS
                            and self.arrivals.greeting_pending()):
                        # October 22: a goal planned in the first seconds was replaced by the greeting's own
                        # plan 9 s later, one planner call wasted every startup. Someone in view is greeted first.
                        self.initiative_gate('greeting_first')
                        return
                    from .agency import PLAN_MIN_INTERVAL_SECONDS
                    if (now >= agency.planner_retry_at and not _after_planning and
                            now-agency.plan_started >= PLAN_MIN_INTERVAL_SECONDS):
                        planning_only = True  # Prepared in the background; executed at a lull.
                    elif quiet_seconds < self.lull_seconds or not agency.due(now):
                        self.initiative_gate('planner_retry_wait')
                        return
                elif agency.wants_next_plan(now) and not self.session.planning_active() and not _after_planning:
                    planning_only = True  # The next plan, written while this goal's last beats run.
                else:
                    with self.session._lock:
                        ready = agency.ready(now, quiet_seconds, self.lull_seconds)
                    if not ready:
                        # The beat's own wake-up has not come (or its moment passed and it was let go).
                        self.initiative_gate('goal_waiting:' + str(agency.waiting))
                        return
            from .proactive import Opportunity
            from uuid import uuid4
            source = self.session.state.conversation_source
            if event is None:
                event = Opportunity(uuid4().hex,self.session.session_id,now,None,None,audience,
                    self.session._latest_speech,False,'reconsideration',source,quiet_seconds,
                    audience_key=audience_key)
            # Coalesce perception into the next beat. Each feed item/frame no longer
            # creates its own model attempt or consumes a conversation budget.
            cues = ()
            if self.observations:
                # Interest news is searched and offered only for the recognized people in view.
                from .news import interest_topics
                with self.session._lock:
                    topics = interest_topics(self.session.state.store, self.arrivals.faces)
                self.observations.want_topics(topics)
                cues = tuple(c for c in self.observations.candidates(topics=topics)
                             if self.session.state.observation_available(c.public(), self.arrivals.faces))
            # The planner gets the current frame even when it was already offered or the view
            # is unchanged; review October 3 found it planning blind after the first goal.
            scene = (self.scene_observer.snapshot(True) if planning_only else
                     self.scene_observer.snapshot()) if self.scene_observer else None
            event = replace(event, public_cues=cues, scene_id=scene[0] if scene else None,
                            scene_images=scene[1] if scene else ())
            if self.observations and not planning_only:
                self.observations.attempted([c.cue_id for c in cues])
            if scene and not planning_only:
                self.scene_observer.attempted(scene[0])
            revision = self.session.revision
            self.initiative_gate('preparing_goal' if planning_only else 'greeting' if event.kind in {'visual_arrival','pet_arrival'} else 'pursuing_goal')
        self.trace.emit('agency_preparation_opportunity' if planning_only else 'proactive_opportunity',event_id=event.event_id,kind=event.kind,
                        face_track_id=event.target_track,person_id=event.person_id)
        # Faces in view when the beat began: a newcomer is one more face, not a new track ID.
        def company(at):
            # A tiny or weak detection (a portrait, a false positive) is not a newcomer.
            return [f for f in self.arrivals.faces if 0 <= at-f['at'] <= 1.5 and
                    (f.get('identity') or (f.get('face_width_px') or 48) >= 48)]
        visible_at_start = max(len(event.faces), len(company(now)), 1)
        def valid():
            nonlocal invalidation_reason
            # Sample the producer timestamp before the clock: a microphone frame
            # arriving between these reads must not look like future audio.
            audio_seen_at = self.audio_seen_at
            now = self.trace.clock()
            audio_age = now-audio_seen_at
            # The camera worker may have published a newer analyzed frame since
            # the main loop last polled. Validate the latest evidence, not that
            # older polling snapshot. This reads cache only; no device work.
            self.arrivals.observe(self.camera.latest(now) if self.camera else (),now)
            present.saw_faces(self.arrivals.faces, now)
            intention = self.session.state.conversation_intent.record
            from .conversation_intent import audience
            # A committed silent move (wheels or a pose) finishes like audible speech: its own
            # motor noise and camera reframe must not cancel it. A spoken stop still halts it.
            committed = self.session.motion_committed
            intent_audience_changed = (not committed and intention is not None and intention.source_id == event.event_id
                and intention.audience != audience({'faces':self.arrivals.faces})
                and (self.session._audience_bound_output or bool(intention.memory_ids)))
            memory_person = self.session._memory_subject
            fresh = company(now)
            known = {f.get('identity') for f in fresh if f.get('identity')}
            question_track = self.session._question_track
            question_lost = False
            if question_track and not any(f['track_id'] == question_track for f in fresh):
                # The camera renumbers one face every few seconds. The question still has its
                # person when someone of the same identity (or, for a stranger, any unrecognized
                # face) is in view; it is lost when they are gone or replaced by someone known.
                asked = next((f.get('identity') for f in event.faces if f['track_id'] == question_track), None)
                question_lost = not any(f.get('identity') == asked for f in fresh)
            # Motor noise is short; only sustained speech interrupts a move already under way.
            spoke = (self.session._resumed_speech > event.speech_marker if committed else
                     self.session._latest_speech != event.speech_marker)
            # Queued audio interrupts a beat still being prepared only while it may be words:
            # confirmed speech, or a brief sound recognition then trusted. His own motors heard
            # as "Okay." are recognized, found unreliable and never answered; the beat goes on.
            words = not committed and self.words_on_the_way()
            reason = ('intent_audience_changed' if intent_audience_changed else
                      'shutdown' if self.stop.is_set() else
                      'human_speech' if spoke else
                      'capture_queued' if words and not self.captures.empty() else
                      'turn_queued' if words and not self.turns.empty() else
                      'recognition_busy' if words and self.asr_busy.is_set() else
                      'audio_timestamp_ahead' if audio_age<0 else
                      'audio_stale' if audio_age>.5 else
                      # Identity, not track IDs: renumbered faces of the same person are the same visit.
                      'arrival_left' if (not committed and event.kind=='visual_arrival' and
                          not self.arrivals.still_present(event, now)) else
                      'goal_person_left' if (not committed and self.session.agency_scope=='audience' and
                          not agency.subject_present(now)) else
                      'question_target_left' if (not committed and question_lost) else
                      # Private material waits when someone else joins or replaces its person.
                      'audience_changed_or_stale' if (not committed and (self.session._audience_bound_output or
                          self.session.agency_scope=='audience') and (
                          (memory_person and known and memory_person not in known) or
                          len(fresh) > visible_at_start)) else None)
            if reason and not invalidated.is_set():
                invalidation_reason = reason
                invalidated.set()
                self.trace.emit('proactive_invalidated',event_id=event.event_id,kind=event.kind,
                                reason=reason,audio_age_seconds=audio_age)
            return reason is None
        invalidated = threading.Event()
        invalidation_reason = None
        human_turns = self.session.human_turns
        result = self.session.process_opportunity(event,revision,valid,planning_only=planning_only)
        if planning_only:
            # Planning normally takes seconds and continues in the background. A goal that is
            # ready almost at once is acted on in this same tick when the lull allows.
            self.session.join_planning(PLANNING_GLANCE_SECONDS)
            if not self.session.planning_active():
                return self.proactive_tick(_after_planning=True) or result
            return result
        self.arrivals.finished(event,result,cut_off_turns=human_turns if invalidation_reason in NOISE_CUTOFFS else None)
        if event.kind == 'visual_arrival' and result.status not in {'rejected'}:
            with self.session._lock:
                current = agency.record if agency.applicable(self.trace.clock()) else None
                if not (event.person_id and current and current.get('subject_person') == event.person_id):
                    # A person arriving is the more interesting goal: plan the visit with them.
                    agency.replace('person_arrived', {'person_id': event.person_id, 'track_id': event.target_track})
        if result.status == "interrupted" and not planning_only:
            agency.due_at = max(agency.due_at, self.trace.clock() + self.lull_seconds)
        self.trace.emit('proactive_opportunity_done',event_id=event.event_id,kind=event.kind,status=result.status,medium=result.medium,
                        delivered=bool(result.completed_text),failed=result.error)
        return result

    def start_workers(self):
        def guarded(target):
            try:
                target()
            except BaseException as exc:
                self.stop.set()
                self.session.closed = True
                self.asr_cancel.event.set()
                try:
                    self.session.cancel('shutdown' if self.stop.is_set() else 'explicit_stop')
                    self.trace.emit("worker_stuck",worker=threading.current_thread().name,reason=type(exc).__name__)
                except Exception:
                    print("Core worker stopped: "+type(exc).__name__,file=sys.stderr)
        for name, target in (("asr", self._asr_loop), ("conversation", self._turn_loop)):
            thread = threading.Thread(target=guarded,args=(target,), name="r3x-" + name, daemon=True)
            self.workers.append(thread)
            thread.start()

    def run(self):
        from .input_health import InputHealth
        health = InputHealth(self.trace,getattr(self.mic,'device_name','configured_microphone'))
        self.start_workers()
        self.trace.emit("listening", controls="Enter or 'stop' cancels; say 'shut down', type 'quit', or Ctrl-C to exit")
        previous_dropped = (0, 0)
        previous_end = None
        previous_echo = False
        vad_fresh_at = None
        direction = getattr(self.perception, 'direction', None)
        reflexed = False
        peeked = False           # This utterance was already checked for a stop while rolling.
        run = 0.                 # Continuous voiced time ending at the latest frame.
        from collections import deque
        recent = deque()         # (ended_at, seconds) of voiced frames in the last second.
        next_resources = 0.
        next_visual = 0.
        # Nonblocking terminal input is polled alongside frames; no orphan stdin worker.
        import select
        try:
            while not self.stop.is_set():
                if self.robot:
                    frames = self.endpoint.frames
                    # Confirmed speech stills him to listen. A suspected sound does not,
                    # unless it keeps the capture open: then a still moment lets it end.
                    capturing = bool(frames) and (self.speech_confirmed or
                        self.audio_seen_at - frames[0].started_at >= SUSPECT_OPEN_SECONDS)
                    conversation_busy = self.session._processing.locked() and not self.session.preparing_plan
                    # Idle life continues while he thinks or works; it stays calm then
                    # (no view-changing head moves, the microphone arm at rest).
                    working = (conversation_busy or self.asr_busy.is_set() or not self.captures.empty()
                               or not self.turns.empty())
                    self.robot.before_frame(capturing=capturing or conversation_busy, calm=working, allow_idle=(
                        not capturing and not self.guarding()   # Sentry mode: the arms stop moving completely.
                        # Only while the wheels turn: the second after a drive is not a reason to sit still.
                        and not (self.drive is not None and self.drive.moving.is_set())
                        and self.trace.clock()-self.audio_seen_at<=.5))
                now = self.trace.clock()
                if now>=next_visual:
                    self.observe_visual(now)
                    next_visual = now+.25
                if now>=next_resources:
                    import resource
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    self.trace.emit("resources",peak_rss_bytes_macos=usage.ru_maxrss,
                                    user_cpu_seconds=usage.ru_utime,system_cpu_seconds=usage.ru_stime,
                                    captures_queued=self.captures.qsize(),turns_queued=self.turns.qsize(),
                                    threads=threading.active_count())
                    next_resources = now+30
                if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
                    command = sys.stdin.readline().strip().lower()
                    if command in {"quit", "exit", "shut down", "shutdown"}:
                        break
                    if command in {"", "stop", "cancel"}:
                        self.cancel()
                frame = self.mic.read()
                if frame is None:
                    continue
                if self._wake_at is not None:
                    # A wake word cut his line: past the moment the sound stops, what is heard is the
                    # person's, not his echo, until he starts another line.
                    output = self.session.output
                    if (frame.started_at - self._wake_at > WAKE_LISTEN_SECONDS or
                            (getattr(output, 'active', None) is not None and output.active.is_set()
                             and frame.started_at > self._wake_at + WAKE_SPEAKING_AGAIN_SECONDS)):
                        self._wake_at = None
                    elif frame.echo_risk and frame.started_at >= self._wake_at + WAKE_CUT_SECONDS:
                        frame = replace(frame, echo_risk=False)
                # ADC timestamps can run slightly ahead of monotonic time after
                # the audio device changes streams. Liveness cannot be newer than
                # receipt; preserve original frame times for capture/attribution.
                self.audio_seen_at = min(frame.ended_at, self.trace.clock())
                if self.guarding():
                    self.sentry.audio(frame)   # The last ten seconds wait for a face to arrive.
                if self.recorder and frame.started_at>=self.started_at:
                    try:
                        path=self.recorder.record_input(frame)
                        if path:self.trace.emit('input_diagnostic_saved',path=str(path))
                    except Exception as exc:
                        self.trace.emit('recording_failed',kind='input_diagnostic',error_type=type(exc).__name__)
                counts = (self.mic.dropped, self.mic.status_errors)
                if counts != previous_dropped or (previous_end is not None and frame.started_at - previous_end > .05):
                    self.trace.emit("capture_gap", dropped=counts[0], status_errors=counts[1],
                        gap_seconds=frame.started_at-previous_end if previous_end is not None else None,
                        utterance_discarded=bool(self.endpoint.frames))
                    self.endpoint.reset()
                    self.speech_confirmed = False
                    self.vad.reset()
                    previous_dropped = counts
                previous_end = frame.ended_at
                if frame.started_at <= self.cancel_before:
                    self.endpoint.reset()
                    self.speech_confirmed = False
                    continue
                # Silero's recurrent state drifted numb after long echo/servo noise
                # (September 27: live 2 speech frames where a fresh model found 30).
                # Reset when playback ends and every 4 s of quiet with nothing pending;
                # the 1 s pre-roll keeps any onset the fresh state has to re-learn.
                if (previous_echo and not frame.echo_risk) or (not self.endpoint.frames and
                        vad_fresh_at is not None and frame.started_at - vad_fresh_at >= 4.):
                    self.vad.reset()
                    vad_fresh_at = frame.started_at
                if vad_fresh_at is None or self.endpoint.frames:
                    vad_fresh_at = frame.started_at
                previous_echo = frame.echo_risk
                speech = self.vad.is_speech(frame.pcm)
                if (not speech and not frame.echo_risk and self.drive is not None and self.drive.moving.is_set()
                        and (getattr(self.vad, 'last_probability', None) or 0.) >= DRIVE_SPEECH_PROBABILITY):
                    speech = True
                if self.keywords is not None:
                    self._listen_for_keywords(frame)
                seconds = frame.ended_at - frame.started_at
                run = run + seconds if speech else 0.
                while recent and frame.ended_at - recent[0][0] > 1.:
                    recent.popleft()
                if speech:
                    recent.append((frame.ended_at, seconds))
                    if (not frame.motion_risk or frame.echo_risk or run >= CONFIRM_RUN_SECONDS
                            or sum(s for _, s in recent) >= CONFIRM_RECENT_SECONDS):
                        self.speech_confirmed = True
                if speech and self.robot and self.speech_confirmed:
                    # Speech stops decorative idle motion so the rest is heard cleanly.
                    # Existing echo/hold guards still exclude it from learning.
                    self.robot.note_speech()
                health.observe(frame,speech,self.endpoint.frames,
                    vad_probability=getattr(self.vad,'last_probability',None),
                    wheels_moving=self.drive is not None and self.drive.moving.is_set())
                if (speech and self.speech_confirmed and not frame.echo_risk
                        and not (self.drive is not None and self.drive.moving.is_set())):
                    # Motor noise or talk while the wheels run does not yield the plan;
                    # the capture still becomes a turn, and a spoken stop still halts.
                    voiced = (self.endpoint.voiced if self.endpoint.frames else 0.) + frame.ended_at - frame.started_at
                    self.session.note_speech(frame.ended_at, sustained=voiced >= YIELD_VOICED_SECONDS)
                    self.voice_over_music()
                    if self.robot and direction and not reflexed and voiced >= REFLEX_VOICED_SECONDS:
                        began = self.endpoint.frames[0].started_at if self.endpoint.frames else frame.started_at
                        evidence = direction.during(began, frame.ended_at)
                        if evidence['status'] == 'observed':
                            reflexed = True   # Once per utterance, from its first clear bearing.
                            outcome = self.robot.reflex.face(evidence['bearing_deg'])
                            if outcome != 'started':
                                self.trace.emit('voice_reflex_skipped', reason=outcome,
                                                bearing_deg=round(evidence['bearing_deg'], 1))
                confirmed = self.speech_confirmed
                capture = self.endpoint.push(frame, speech)
                if (not capture and not peeked and not speech and self.endpoint.frames
                        and self.drive is not None and self.drive.moving.is_set()
                        and 0 < self.endpoint.voiced <= STOP_PEEK_MAX_VOICED
                        and frame.ended_at - self.endpoint.last_speech >= STOP_PEEK_SILENCE):
                    peeked = True
                    burst = self.endpoint.frames
                    self._stop_peek(Capture(uuid4().hex, self.session.session_id, b''.join(f.pcm for f in burst),
                                            burst[0].started_at, self.endpoint.last_speech, burst[-1].ended_at,
                                            echo_risk=any(f.echo_risk for f in burst), motion_risk=True),
                                    self.session.revision)
                if (capture and capture.echo_risk and not peeked and not self.counting_down()
                        and self.drive is not None and self.drive.moving.is_set()):
                    # Said over his own line as he rolls: the conversation rejects it as echo,
                    # so only the stop check hears it.
                    self._stop_peek(capture, self.session.revision)
                if not self.endpoint.frames:
                    reflexed = False
                    peeked = False
                    self.speech_confirmed = False
                if capture:
                    capture = replace(capture,device=getattr(self.mic,"source_device",getattr(self.mic,"device_name","configured_microphone")),
                                      self_noise_risk=not confirmed)
                    if self.robot:
                        capture = replace(capture,robot_microphone_hold=self.robot.capture_held(capture))
                    if self.recorder:
                        self.recorder.save(capture)
                    self.trace.emit("capture", capture_id=capture.capture_id,
                        started_at=capture.started_at, ended_at=capture.ended_at,
                        speech_ended_at=capture.speech_ended_at, truncated=capture.truncated,
                        echo_risk=capture.echo_risk, motion_risk=capture.motion_risk,
                        self_noise_risk=capture.self_noise_risk, pcm_bytes=len(capture.pcm), ended_by=capture.ended_by,
                        vad_voiced_seconds=sum(b-a for a,b in capture.voiced_intervals),
                        robot_microphone_hold=capture.robot_microphone_hold)
                    self.enqueue(self.captures, (capture, self.session.revision), "captures")
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            # A stalled microphone or device error ends the run: record why.
            self.trace.emit('runtime_failed', error_type=type(exc).__name__,
                            reason=str(exc) if isinstance(exc, ProviderFailure) else None)
            raise
        finally:
            self.close()

    def close(self):
        self.stop.set()
        self.cancel()
        self.mic.cancel()
        if getattr(self, 'sentry', None) is not None:
            try:
                self.sentry.close()   # Finishes a recording before the servos park.
            except Exception as exc:
                self.trace.emit('sentry', status='close_failed', error_type=type(exc).__name__)
        if self.recorder:
            try:
                path=self.recorder.close()
                if path:self.trace.emit('input_diagnostic_saved',path=str(path))
            except Exception as exc:
                self.trace.emit('recording_failed',kind='input_diagnostic',error_type=type(exc).__name__)
        # Cooperative workers stop within this; a stuck one is left behind and the process
        # exits without it (the hardware still parks first).
        deadline = time.monotonic() + SHUTDOWN_WORKER_SECONDS
        for worker in self.workers:
            worker.join(max(0, deadline - time.monotonic()))
        stuck = [w.name for w in self.workers if w.is_alive()]
        output_worker = self.session.output_worker
        if output_worker is not None and output_worker.is_alive():
            stuck.append(output_worker.name)
        if stuck:
            self.trace.emit("worker_stuck", workers=stuck)
            raise ProviderFailure("RuntimeWorkersDidNotStop")
        self.session.closed = True
