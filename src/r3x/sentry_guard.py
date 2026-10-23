"""Runs sentry mode (owner request, October 22): lights, body, countdown, watching, the alarm,
recordings, phone alerts and the end. sentry.Sentry (held by the state owner) decides; this acts.

Threads: the countdown speaks on its own thread (nothing else speaks then), the body moves on its own
thread through the output owner's motion lock, and everything else runs on the conversation worker
(the runtime calls tick when no turn is waiting and hear for each transcript), so the alarm's lines go
through the one conversation owner and the one output owner like any other speech.

While he guards no paid call is made: the face, voice and speech checks run on the robot, the
countdown and the alarm cry are cached clips, the planner, lull beats, joke writer and news searches
are off, and the wheels are held. Only an intrusion (the roast lines, the alert) or the stand-down
greeting reaches a hosted service.
"""
from collections import deque
from dataclasses import replace
from datetime import datetime
from queue import Queue
import threading
from uuid import uuid4

from .contracts import Cancellation, Cancelled
from .output import Delivery
from . import sentry as rules

NAMES_SECONDS = 10.        # How long the list of known names is reused.
LOOK_EVERY = .25           # Camera samples are judged this often (faces are analyzed about twice a second).
SWEEP_HOLD_SECONDS = 15.   # Each stop of the guard's sweep (owner: turn a little, hold, turn again).
SWEEP_STEP_SECONDS = 3.    # A sweep step's travel.
SURVEY_STEP_SECONDS = 1.2  # The countdown's survey moves quicker.
REFLEX_PAUSE_SECONDS = 20. # After something else turned his head (a voice in front), the sweep waits.
AIM_SECONDS = 1.           # Turning to an intruder.
AIM_DEG = 6.               # An intruder this far from where he looks: turn to them again.
FLASH_SECONDS = 3.
# Endings followed by the program's shutdown: the park and the light fade take over, so nothing is undone first.
SHUTTING_DOWN = frozenset({'shutdown', 'time_up'})


class SentryGuard:
    def __init__(self, session, output, trace, *, camera=None, canned=None, recorder=None, push=None,
                 observations=None, shutdown=None):
        self.session, self.output, self.trace = session, output, trace
        self.state = session.state.sentry
        self.camera, self.canned, self.recorder, self.push = camera, canned, recorder, push
        self.observations = observations
        self.shutdown = shutdown or (lambda reason: None)
        self.clock = trace.clock
        self.lock = threading.Lock()
        self.pending = deque()        # Speech the conversation worker owes the guard.
        self.notes = {}               # Opportunity event_id -> what the model is told for it.
        self._countdown = None        # (thread, Cancellation)
        self._body = None
        self._body_cancel = Cancellation()
        self._body_wake = threading.Event()
        self._release = True          # Off guard: release the stance (not when shutting down: park does).
        self.aim = None               # Bearing (+left) of the intruder to face, or None.
        self.arm_out = False
        self._names = (float('-inf'), {})
        self._looked = float('-inf')
        self._push = None             # {'event_id', 'sent', ...}: the alert for the current intrusion.
        self.push_delay = rules.PUSH_DELAY_SECONDS
        self.crop = None              # (frame, box) -> image; objects.crop_image unless replaced.
        self._expired_at = None
        session.sentry = self

    # Questions from the runtime -----------------------------------------------------
    @property
    def active(self):
        return self.state.active

    def echo_allowed(self):
        """During the count the microphone also hears his own numbers; only a call-off word counts."""
        return self.state.phase == 'countdown'

    def audio(self, frame):
        """A microphone frame, from the audio loop."""
        if self.recorder is not None:
            self.recorder.audio(frame.pcm, frame.started_at)

    def holding_wheels(self):
        return 'sentry_mode' if self.state.active else None

    def known(self, person_id):
        """A saved person's name, or None (no name: not someone he knows)."""
        at, names = self._names
        if self.clock() - at >= NAMES_SECONDS:
            with self.session._lock:
                names = {p['id']: p['name'] for p in self.session.state.store.people()
                         if p.get('name') and p.get('status') not in {'retracted'}}
            self._names = (self.clock(), names)
        return names.get(person_id)

    # Starting and ending ------------------------------------------------------------
    def start(self, data, turn_id, requester=None):
        plan = rules.SentryPlan(data['countdown_seconds'], data['ends_at'], data['audio_alerts'],
                                data['requested_at'], data.get('until_text'))
        with self.session._lock:
            self.state.begin(plan, requester)
        self._release, self.aim, self.arm_out, self._push, self._expired_at = True, None, False, None, None
        self.pending.clear()
        self.trace.emit('sentry', status='countdown', turn_id=turn_id, requester=requester, **plan.public())
        self._lights(True)
        if self.observations is not None:
            self.observations.paused = True   # No paid news searches while he guards.
        if self.recorder is not None:
            self.recorder.start()   # The last ten seconds wait in memory; nothing is saved yet.
        self._body_cancel = Cancellation()
        self._body = threading.Thread(target=self._run_body, name='r3x-sentry-body', daemon=True)
        self._body.start()
        cancel = Cancellation()
        thread = threading.Thread(target=self._run_countdown, args=(plan, cancel, turn_id),
                                  name='r3x-sentry-countdown', daemon=True)
        self._countdown = (thread, cancel)
        thread.start()

    def end(self, reason, person_id=None):
        """Off guard, for any reason. Returns the intrusion that was running, if any."""
        with self.session._lock:
            if not self.state.active:
                return None
            previous, event = self.state.end(reason)
        self._release = reason not in SHUTTING_DOWN
        if self._countdown is not None:
            self._countdown[1].event.set()
        if reason in SHUTTING_DOWN:
            self._body_cancel.event.set()   # A move under way holds where it is; the park follows.
        self._body_wake.set()
        self.pending.clear()
        self.aim, self.arm_out = None, False
        if self.observations is not None:
            self.observations.paused = False
        if reason not in SHUTTING_DOWN:
            self._lights(False)
        if self.recorder is not None:
            details = {'ended': reason, **({'stood_down_by': self.known(person_id)} if person_id else {}),
                       **({'heard': list(event.heard)} if event else {})}
            if reason in SHUTTING_DOWN:
                self.recorder.stop(details)
            else:
                threading.Thread(target=self.recorder.stop, args=(details,), name='r3x-sentry-video', daemon=True).start()
        with self.lock:
            push, self._push = self._push, None
        if push and push.get('sent') and person_id and self.push is not None:
            name = self.known(person_id) or 'someone he knows'
            self._send_push('Sentry mode stood down', f'{name} is here; Rex stood down.', None, priority='default',
                            tags='white_check_mark')
        self.trace.emit('sentry', status='off', reason=reason, previous=previous, person_id=person_id,
                        intrusion=event.event_id if event else None)
        return event

    def close(self):
        """Shutdown: stop every worker; the park and the light fade follow in the providers' close."""
        self.end('shutdown')
        for worker in (self._countdown[0] if self._countdown else None, self._body):
            if worker is not None:
                worker.join(3.)

    def _lights(self, on):
        leds = self.output.leds
        if leds is None:
            return
        try:
            leds.guard(on)
        except Exception as exc:
            self.trace.emit('sentry', status='lights_failed', on=on, error_type=type(exc).__name__)

    # Speech ---------------------------------------------------------------------------
    def _clip(self, text, cancel):
        from .startup_audio import Prerecorded
        try:
            return Prerecorded(text, self.canned(text, cancel)) if self.canned else text
        except Cancelled:
            raise
        except Exception as exc:
            self.trace.emit('sentry', status='clip_failed', text=text, error_type=type(exc).__name__)
            return text   # Spoken live instead.

    def _say(self, lines, cancel, turn_id):
        """Play lines through the output owner now, on this thread. A failure is logged, never fatal."""
        phrases, delivery = Queue(), Delivery()
        for line in lines:
            phrases.put(line)
        phrases.put(None)
        try:
            self.output.run(phrases, cancel, turn_id, delivery)
        except Exception as exc:
            delivery.status, delivery.error = 'failed', type(exc).__name__
        if delivery.status not in {'completed', 'interrupted'}:
            self.trace.emit('sentry', status='speech_failed', turn_id=turn_id, error=delivery.error)
        return delivery

    def _run_countdown(self, plan, cancel, turn_id):
        calls = rules.countdown_calls(plan.countdown_seconds)
        try:
            opening = self._clip(rules.OPENING, cancel)
            numbers = {n: self._clip(rules.spoken_number(n), cancel) for n in calls}
            active = self._clip(rules.ACTIVE, cancel)
            self._say([rules.confirmation(plan)], cancel, turn_id + ':sentry-plan')
            self._say([opening], cancel, turn_id + ':sentry-opening')
            start = self.clock()
            for n in calls:
                self._wait(start + plan.countdown_seconds - n, cancel)
                self._say([numbers[n]], cancel, turn_id + f':sentry-{n}')
            self._wait(start + plan.countdown_seconds, cancel)
            with self.session._lock:
                if self.state.phase != 'countdown':
                    return
                self.state.arm(self.clock())
            self.trace.emit('sentry', status='guarding', fixtures=len(self.state.fixtures),
                            people_at_arming=sorted(self.state.arming_people))
            self._body_wake.set()
            self._say([active], Cancellation(), turn_id + ':sentry-active')
        except Cancelled:
            self.trace.emit('sentry', status='countdown_stopped', turn_id=turn_id)

    def _wait(self, at, cancel):
        while True:
            cancel.check()
            left = at - self.clock()
            if left <= 0:
                return
            cancel.event.wait(min(.05, left))

    def call_off(self, how):
        """A spoken call-off during the countdown: stop counting, say so, back to normal."""
        if self.state.phase != 'countdown':
            return
        self.end('called_off')
        if self._countdown is not None:
            self._countdown[0].join(3.)
        self._say([self._clip(rules.CANCELLED, Cancellation())], Cancellation(), 'sentry-called-off')
        self.session.history.append({'role': 'developer', 'content':
            'Sentry mode was called off during its countdown (' + how + '); you said "Sentry mode cancelled." '
            'You are back to normal.'})

    # The body -------------------------------------------------------------------------
    def _move(self, move, wait=1.):
        """One body move through the output owner; True when its pulses arrived."""
        if self.output.robot is None:
            return False
        try:
            receipt = self.output.sentry_motion(move, self._body_cancel, wait=wait)
        except Cancelled:
            return False
        except Exception as exc:
            self.trace.emit('sentry', status='move_failed', error_type=type(exc).__name__)
            return False
        return receipt is not None and receipt.status == 'pulses_reached'

    def _neck(self):
        robot = self.output.robot
        return robot.owner.neck_degrees() if robot is not None else 0.

    def neck_settled(self, rows):
        """Where the head points for these camera rows, or None while the view is unsettled."""
        robot = self.output.robot
        if robot is None:
            return 0.
        owner = robot.owner
        if owner.camera_moving or any(r.get('at', 0.) < owner.head_moved_at + .3 for r in rows):
            return None
        return owner.neck_degrees()

    def _run_body(self):
        from .drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
        stops = rules.SWEEP_STOPS_DEG
        pulse = lambda deg: NECK_CENTER - max(-45., min(45., deg)) * NECK_PULSES_PER_DEG
        self._move(lambda robot, cancel: robot.sentry_stance(cancel), wait=5.)
        survey_hold = max(3., self.state.plan.countdown_seconds / len(stops) - SURVEY_STEP_SECONDS) if self.state.plan else 3.
        index, step = 0, 1
        target_deg, moved_at, arm_is_out = None, float('-inf'), False
        paused_until = float('-inf')
        while self.state.active and not self._body_cancel.event.is_set():
            now = self.clock()
            phase = self.state.phase
            neck = self._neck()
            if target_deg is not None and abs(neck - target_deg) > 2. and now - moved_at > 1.:
                paused_until, target_deg = now + REFLEX_PAUSE_SECONDS, neck   # A voice turned his head.
            wanted = self.arm_out
            if wanted != arm_is_out and self._move(lambda robot, cancel: robot.sentry_arm(wanted, cancel)):
                arm_is_out = wanted
            aim = self.aim
            if aim is not None and phase in rules.GUARD_PHASES:
                aim = max(-45., min(45., aim))
                if target_deg is None or abs(aim - target_deg) > AIM_DEG:
                    if self._move(lambda robot, cancel, aim=aim: robot.sentry_look(pulse(aim), cancel, AIM_SECONDS, 'sentry:aim')):
                        target_deg, moved_at = aim, self.clock()
            elif phase == 'countdown' and index < len(stops) and now - moved_at >= survey_hold:
                if self._move(lambda robot, cancel, deg=stops[index]: robot.sentry_look(pulse(deg), cancel, SURVEY_STEP_SECONDS,
                                                                                         'sentry:survey')):
                    target_deg, moved_at = stops[index], self.clock()
                    index += 1
            elif phase == 'guarding' and now >= paused_until and now - moved_at >= SWEEP_HOLD_SECONDS:
                if index >= len(stops) or index < 0:
                    index, step = (len(stops) - 2, -1) if index >= len(stops) else (1, 1)
                if self._move(lambda robot, cancel, deg=stops[index]: robot.sentry_look(pulse(deg), cancel, SWEEP_STEP_SECONDS,
                                                                                         'sentry:sweep')):
                    target_deg, moved_at = stops[index], self.clock()
                    index += step
                    if not 0 <= index < len(stops):
                        step = -step
                        index += 2 * step
            self._body_wake.wait(.25)
            self._body_wake.clear()
        if self._release:
            self._move(lambda robot, cancel: robot.sentry_release(cancel), wait=8.)

    # The conversation worker's part ----------------------------------------------------
    def tick(self, now):
        """No turn is waiting: judge the camera, run the alarm, send the alert, watch the clock."""
        phase = self.state.phase
        if phase == 'off':
            return
        if self.state.expired():
            if phase in {'countdown', 'guarding'} or (self._expired_at is not None and
                                                       now - self._expired_at >= rules.EXPIRY_WAIT_SECONDS):
                self.trace.emit('sentry', status='time_up', ends_at=self.state.plan.ends_at)
                self.end('time_up')
                self.shutdown('sentry_time_up')   # Owner, October 22: when the time runs out he shuts down.
                return
            if self._expired_at is None:
                self._expired_at = now   # Mid-alarm: the recording finishes first.
        # The camera first: someone he knows standing there stands him down before any alarm line is said
        # (October 22: recognized 0.6 s before the roast, he shouted "Oh, Brett—it's you. Stand-down" in alarm).
        if (now - self._looked >= LOOK_EVERY or self.pending) and self.camera is not None:
            self._looked = now
            rows = self.camera.latest(now)
            neck = self.neck_settled(rows)
            with self.session._lock:
                decisions = (self.state.survey(rows, now, neck) if phase == 'countdown' else
                             self.state.faces(rows, now, neck, self.known))
            for decision in decisions:
                self._act(decision, now)
                if not self.state.active:
                    return
        if self.pending:
            self._speak(self.pending.popleft())
            return
        with self.session._lock:
            quiet = self.state.quiet_down(now)
            jab = self.state.jab_due(now)
        if quiet:
            self.arm_out = False
            self._body_wake.set()
            self.trace.emit('sentry', status='watching', event_id=self.state.event.event_id)
        elif jab:
            self.pending.append(('sentry_jab', self.state.event.event_id))

    def _act(self, decision, now):
        kind = decision.kind
        if kind == 'fixture':
            self.trace.emit('sentry', status='picture_ignored', bearing_deg=decision.bearing)
        elif kind == 'seen':
            if decision.bearing is not None and (self.state.phase in {'alarm', 'watching'} or self.state.arrived):
                self.aim = decision.bearing
                self._body_wake.set()
        elif kind == 'arrived':
            self.trace.emit('sentry', status='face_arrived', bearing_deg=decision.bearing,
                            width_px=(decision.face or {}).get('face_width_px'))
            if decision.bearing is not None:
                self.aim = decision.bearing
                self._body_wake.set()
            if self.recorder is not None:
                self.recorder.begin('face')   # Owner: only faces arriving are recorded.
        elif kind == 'trip':
            self._trip(decision.reason, decision.bearing)
        elif kind == 'stand_down':
            self._stand_down(decision.person_id, decision.reason)
        elif kind == 'gone':
            with self.session._lock:
                event = self.state.all_clear()
            self.aim, self.arm_out = None, False
            self._body_wake.set()
            self.pending.clear()
            if self.recorder is not None:
                threading.Thread(target=self.recorder.finish, args=({'ended': 'intruders_left',
                                 'heard': list(event.heard)},), name='r3x-sentry-video', daemon=True).start()
            self.trace.emit('sentry', status='all_clear', event_id=event.event_id)

    def _trip(self, reason, bearing, heard=None):
        now = self.clock()
        with self.session._lock:
            event = self.state.trip(reason, now, uuid4().hex)
            if heard:
                event.heard.append(heard.strip()[:300])
                event.exchange.append(('intruder', heard.strip()[:300]))
                event.last_heard = now
        self.trace.emit('sentry', status='tripped', event_id=event.event_id, reason=reason, bearing_deg=bearing,
                        audio_alerts=self.state.plan.audio_alerts)
        if bearing is not None:
            self.aim = bearing
        self.arm_out = True
        self._body_wake.set()
        if self.output.leds is not None:
            self.output.leds.flash(FLASH_SECONDS)
        with self.lock:
            self._push = {'event_id': event.event_id, 'sent': False, 'reason': reason, 'wall': event.wall_at}
        # Its own timer: the alert goes 10 s after the trip even while he talks (October 22: 22 s, behind a line).
        timer = threading.Timer(self.push_delay, self._alert, args=(event.event_id,))
        timer.daemon = True
        timer.start()
        if self.state.plan.audio_alerts:
            self.pending.append(('sentry_alarm', event.event_id))

    def _stand_down(self, person_id, via):
        name = self.known(person_id) or 'someone he knows'
        event = self.end('stood_down', person_id)
        intrusion = (f'Before they came, someone you did not recognize tripped the alarm '
                     f'({event.reason}){": they said " + "; ".join(event.heard[-3:]) if event and event.heard else ""}. '
                     if event else '')
        if via == 'face':
            # Now: once he is off guard the runtime no longer ticks the guard. They are seen and, once the
            # line is heard, spoken with: the ordinary arrival greeting does not greet them a second time.
            if self.camera is not None:
                self.session.state.present.saw_faces(self.camera.latest(self.clock()), self.clock())
            delivery = self._speak(('sentry_stand_down', rules.STAND_DOWN_NOTE.format(name=name, intrusion=intrusion),
                                    person_id))
            if delivery is not None and delivery.completed_text:
                self.session.state.present.rex_said(' '.join(delivery.completed_text), self.clock(),
                                                    own_initiative=True, answering=person_id)
        else:
            # Their own words follow as an ordinary turn, which greets them first.
            self.session.history.append({'role': 'developer', 'content':
                rules.VOICE_STAND_DOWN_NOTE.format(name=name, intrusion=intrusion)})

    def _speak(self, item):
        """An alarm line, a jab or the stand-down greeting, through the conversation owner."""
        from .proactive import Opportunity
        kind = item[0]
        opportunity_id = uuid4().hex
        if kind == 'sentry_stand_down':
            if self.state.active:
                return
            note, person = item[1], item[2]
            valid = lambda: not self.session.closed and not self.state.active
        else:
            event_id, event = item[1], self.state.event
            if event is None or event.event_id != event_id or self.state.phase != 'alarm':
                return
            if kind == 'sentry_alarm':
                if self._known_now():
                    return
                self._say([self._clip(rules.ALERT, Cancellation())], Cancellation(), event_id + ':alert')
                event.exchange.append(('rex', rules.ALERT))
            if self._known_now():   # Recognized while the cry played: no alarm line for them.
                return
            note, person = None, None
            valid = lambda: (self.state.phase == 'alarm' and self.state.event is not None and
                             self.state.event.event_id == event_id and not self.session.closed)
        self.notes[opportunity_id] = note
        faces = tuple(self.camera.latest(self.clock())) if self.camera is not None else ()
        opportunity = Opportunity(opportunity_id, self.session.session_id, self.clock(), None, person, faces,
                                  self.session._latest_speech, False, kind,
                                  scene_images=() if kind == 'sentry_stand_down' else self._intruder_picture())
        delivery = None
        try:
            delivery = self.session.process_opportunity(opportunity, self.session.revision, valid)
        except Exception as exc:
            self.trace.emit('sentry', status='line_failed', kind=kind, error_type=type(exc).__name__)
        finally:
            self.notes.pop(opportunity_id, None)
        self.trace.emit('sentry', status='line_spoken', kind=kind,
                        heard=' '.join(delivery.completed_text) if delivery else None)
        self.after_reply(delivery)
        return delivery

    def after_reply(self, delivery):
        """A line spoken for the guard (or an answer to an intruder) has finished."""
        event = self.state.event
        if event is not None:
            event.last_said = self.clock()
            if delivery is not None and delivery.completed_text:
                event.exchange.append(('rex', ' '.join(delivery.completed_text)))

    def _known_now(self):
        """Someone he knows is in view right now: stand down (True) instead of raising the alarm at them."""
        if self.camera is None:
            return False
        now = self.clock()
        with self.session._lock:
            person = self.state.known_in_view(self.camera.latest(now), now, self.known)
        if person:
            self._stand_down(person, 'face')
        return bool(person)

    def _intruder_picture(self):
        """A picture of the intruder alone, cropped from his eyes' latest view around the biggest face he
        does not recognize (head and shoulders down to the waist), or nothing when nobody is in view.
        October 22: given the whole room, the roast was about the telescope."""
        if self.camera is None:
            return ()
        now = self.clock()
        from .sentry import face_ok
        strangers = [r for r in self.camera.latest(now) if face_ok(r, now) and not r.get('identity')]
        frame = getattr(getattr(self.camera, 'hub', None), 'peek', lambda: None)()
        if not strangers or frame is None or not -.5 <= now - frame.at <= 1.5:   # Taken just now, or a moment ago.
            return ()
        x, y, w, h = max(strangers, key=lambda r: r['box'][2] * r['box'][3])['box']
        x0, y0 = max(0., x - 1.5 * w), max(0., y - .6 * h)
        x1, y1 = min(1., x + 2.5 * w), min(1., y + 5. * h)
        try:
            if self.crop is None:
                from .objects import crop_image as crop
            else:
                crop = self.crop
            return (crop(frame, [x0, y0, x1 - x0, y1 - y0]),)
        except Exception as exc:
            self.trace.emit('sentry', status='look_failed', error_type=type(exc).__name__)
            return ()

    def reply_context(self, opportunity):
        """What the conversation model is given when it speaks for the guard, or None for ordinary
        conversation: {'context', 'note', 'images', 'messages'}. The guard's lines see only their brief,
        this intrusion's exchange and the intruder (messages replaces the conversation history)."""
        kind = opportunity.kind if opportunity is not None else None
        if kind == 'sentry_stand_down':
            # Back to himself: ordinary context, plus what just happened.
            return {'context': {'phase': 'off', 'stood_down': True}, 'note': self.notes.get(opportunity.event_id),
                    'images': ()}
        event = self.state.event
        if kind in rules.SENTRY_KINDS and event is not None:
            images, line = tuple(opportunity.scene_images), kind
        elif opportunity is None and self.state.phase == 'alarm' and event is not None:
            images, line = self._intruder_picture(), 'answer'
        else:
            return None
        messages = rules.alarm_messages(line, event, bool(images))
        return {'context': self.state.context(self.clock(), intruder_in_view=bool(images)),
                'note': messages[0]['content'], 'images': images, 'messages': messages}

    # Hearing -------------------------------------------------------------------------
    def hear(self, capture, transcript, perceived):
        """A transcript while sentry mode is on. True: handled here (ignored, called off, tripped, stood
        down for a shutdown); False: the conversation owner answers it as an ordinary turn (an
        intruder during the alarm talk, or the person who just stood him down)."""
        from .runtime import SHUTDOWN_COMMANDS
        phase = self.state.phase
        if phase == 'off':
            return False
        text = transcript.text or ''
        trusted = transcript.trusted and not capture.echo_risk
        now = self.clock()
        if phase == 'countdown':
            # His own numbers are heard too: only a call-off word or the exact shutdown counts.
            if transcript.trusted and text.strip().lower().rstrip('.!?') in SHUTDOWN_COMMANDS:
                self.end('shutdown')
                self.shutdown('voice')
            elif transcript.trusted and rules.calls_off(text):
                self.call_off('heard: ' + text.strip()[:80])
            else:
                self.trace.emit('sentry_heard', phase=phase, text=text, outcome='ignored_during_countdown')
            return True
        person, reason = None, 'no_voice_evidence'
        voice = getattr(perceived, 'voice', None) if perceived is not None else None
        if voice is not None and trusted:
            if capture.motion_risk and not voice.motion:
                voice = replace(voice, motion=True)
            with self.session._lock:
                tracker = self.session.state.tracker
                person, reason = tracker.match(voice, self.session.state.store.profiles(tracker.namespace),
                                               transcript.trusted, capture.echo_risk, capture.truncated)
        with self.session._lock:
            decision = self.state.heard(text, trusted, person, now, self.known)
        self.trace.emit('sentry_heard', phase=phase, text=text, trusted=trusted, words=rules.words(text),
                        voice_person_id=person, voice_reason=reason, outcome=decision.kind if decision else 'ignored')
        if decision is None or decision.kind == 'ignored':
            return True
        if decision.kind == 'stand_down':
            if text.strip().lower().rstrip('.!?') in SHUTDOWN_COMMANDS:
                self.end('shutdown', person)   # Someone he knows told him to shut down: straight to the park.
                self.shutdown('voice')
                return True
            self._stand_down(person, 'voice')
            return False
        if decision.kind == 'answer':
            from .session import is_stop_command
            if is_stop_command(text):
                return True   # An intruder's "stop, stop" neither cuts him off nor leaves him quiet afterwards.
        if decision.kind == 'trip':
            direction = getattr(voice, 'direction', None) or {}
            bearing = direction.get('bearing_deg') if direction.get('status') == 'observed' else None
            self._trip('speech', bearing if bearing is None or abs(bearing) <= 90 else None, heard=text)
            return True
        return False   # 'answer': the intruder gets an answer from the conversation owner.

    # Phone alert ---------------------------------------------------------------------
    def _alert(self, event_id):
        """The alert for this intrusion, from its timer, unless someone he knows stood him down first."""
        with self.lock:
            push = self._push
            if not push or push['sent'] or push['event_id'] != event_id:
                return
            push['sent'] = True
        event = self.state.event if self.state.event and self.state.event.event_id == event_id else None
        photo = self.recorder.photo() if self.recorder is not None else None
        if photo and self.recorder is not None:
            self.recorder.save_photo(photo)
        when = datetime.fromtimestamp(push['wall']).strftime('%-I:%M %p')
        heard = event.heard if event is not None else []
        message = (f'{when}: someone Rex does not recognize ' +
                   ('is in view.' if push['reason'] == 'face' else 'is talking near him.') +
                   (f' They said: "{heard[0][:160]}"' if heard else '') +
                   (' Recording.' if self.recorder is not None and self.recorder.recording() else ''))
        self._send_push('Rex: sentry mode triggered', message, photo)

    def _send_push(self, title, message, photo, **options):
        if self.push is None:
            self.trace.emit('push_notification', title=title, outcome='not_configured')
            return
        threading.Thread(target=self.push.send, args=(title, message, photo), kwargs=options,
                         name='r3x-push', daemon=True).start()
