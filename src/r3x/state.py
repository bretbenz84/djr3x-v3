"""One validator/executor for meaning proposals and all persistent transitions."""
from dataclasses import asdict, replace
import hashlib
import json
import time
from collections import deque
from .speakers import Track, cosine
from uuid import uuid4

from .contracts import ToolResult
from .enrollment import Enrollment
from .memory import label
from .semantics import validate


class CoreState:
    def __init__(self, store, tracker, trace, frozen=False, face_invalidator=None):
        self.store, self.tracker, self.trace = store, tracker, trace
        self.enrollments = {}
        self.evidence = {}
        self._restored = False
        self.pending = None
        self.current = None
        self.forgotten = False
        self.frozen = frozen
        from datetime import date
        self.calendar_today = date.today
        self.memory_cursor = 0
        self.observations = None
        self.scene_observer = None
        self.wall_clock = time.time
        self.faces = ()
        self.face_associations = {}
        self.face_learning = {}
        self.face_invalidator = face_invalidator
        self.min_face_epoch = 0
        from .proactive import Initiative
        self.initiative = Initiative()

    def cancel(self):
        # Stopping speech cancels dialogue work, not previously observed evidence.
        # Identity corrections use cancel_association or forget_person explicitly.
        if self.pending:
            self.trace.emit("pending_cancelled", enrollments=0, question=True)
        self.pending = None

    def begin(self, turn_id, capture, transcript, observation, faces=()):
        if not self._restored and not self.frozen:
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM voice_candidates WHERE updated_at<?", (time.time()-86400,))
            for row in self.store.rows("SELECT * FROM voice_candidates WHERE namespace=? ORDER BY updated_at DESC LIMIT 12", (self.tracker.namespace,)):
                if row["person_id"] and self.store.person(row["person_id"])["status"] != "provisional":
                    with self.store.transaction() as conn:
                        conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (row["track_id"],))
                    continue
                proposal = Enrollment.decode(row)
                self.evidence[proposal.track_id] = proposal
                self.tracker.tracks[proposal.track_id] = Track(proposal.track_id, capture.ended_at,
                    deque([s[2].embedding for s in proposal.samples] or [proposal.anchor], maxlen=5), proposal.person_id,
                    "hypothesis" if proposal.person_id else "unresolved")
                if proposal.person_id:
                    self.enrollments[proposal.track_id] = proposal
            self._restored = True
        expired = [key for key, p in self.evidence.items() if time.time()-p.updated_at >= 86400]
        if expired and not self.frozen:
            with self.store.transaction() as conn:
                conn.executemany("DELETE FROM voice_candidates WHERE track_id=?", [(key,) for key in expired])
            for key in expired:
                self.evidence.pop(key, None)
                self.enrollments.pop(key, None)
                self.tracker.tracks.pop(key, None)
        # Candidate groups remain matchable during the retention window even
        # if ordinary conversational tracks would otherwise expire after a pause.
        for key in self.evidence:
            if key in self.tracker.tracks:
                self.tracker.tracks[key].last_at = capture.ended_at
        if capture.overlap is True:
            observation = replace(observation, mixed=True)
        attribution = self.tracker.observe(observation, capture.ended_at,
            self.store.profiles(self.tracker.namespace), transcript.trusted, capture.echo_risk, capture.truncated)
        self.enrollments = {key:p for key,p in self.enrollments.items()
                            if p.track_id in self.tracker.tracks}
        if self.pending and capture.ended_at-self.pending["delivered_at"] > 120:
            self.pending = None
        if capture.parent_capture_id is not None:
            attribution = replace(attribution,learning_eligible=False)
        self.current = (turn_id,capture,transcript,observation,attribution)
        self.faces = tuple(f for f in faces if f.get("face_epoch",0)>=self.min_face_epoch)
        faces = self.faces
        self.face_associations = {k:v for k,v in self.face_associations.items() if any(f["track_id"]==k for f in faces)}
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        self.store.source(turn_id,capture,transcript,attribution,observation,track.person_id if track else None)
        self.forgotten = False
        self.learn_observation()
        self.learn_faces()
        return attribution

    def identity_targets(self, name):
        """Same acoustic authorization for offered actions and execution."""
        try:
            self._source_allowed({}, name)
        except ValueError:
            return []
        attribution = self.current[4]
        targets = [attribution.person_id] if attribution.person_id else []
        provisional = self.enrollments.get(attribution.track_id)
        if name == "rename_person" and provisional:
            targets.append(provisional.person_id)
        return list(dict.fromkeys(targets))

    def context(self, faces=()):
        turn_id,capture,transcript,observation,attribution = self.current
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        speaker_person = self.store.person(attribution.person_id) if attribution.person_id else None
        from .face_attribution import public_face
        return {"source_turn_id":turn_id,"state_revision":self.store.revision,
                "current_utterance":transcript.text,"calendar_date":self.calendar_today().isoformat(),
                "participation":self.initiative.context(self.trace.clock()),
                "identity_action_targets":{name:self.identity_targets(name) for name in ("rename_person","forget_person")},
                "speaker":{**asdict(attribution),"display_name":speaker_person["name"] if speaker_person else None},
                "provisional_person_id":track.person_id if track else None,
                "people":self.store.people(),"claims":self.store.claims(include_candidates=True),
                "pending_question":self.pending,"faces":[{**public_face(f),"person_hypothesis":self.face_associations.get(f["track_id"])} for f in self.faces],
                "face_enrollment":[{"face_track_id":k,"person_id":v["person_id"]} for k,v in self.face_learning.items()],
                "frozen_evaluation":self.frozen,
                "transcript_trusted":transcript.trusted,
                "acoustic_limits":{"overlap_detector":"window consistency only", "overlap":capture.overlap,
                    "speaker_boundary_proposed":capture.parent_capture_id is not None},
                "enrollment":[{"person_id":e.person_id,"track_id":e.track_id,"qualifying_turns":len(e.samples),
                               "voiced_seconds":sum(s[2].voiced_seconds for s in e.samples)} for e in self.enrollments.values()]}

    def opportunity_context(self, event):
        """No acoustic observation/source turn is fabricated for an opportunity."""
        from .face_attribution import public_face
        if self.frozen: raise ValueError("FrozenEvaluationDisallowsProactiveSpeech")
        if self.pending and event.at-self.pending['delivered_at']>120: self.pending=None
        faces = [public_face(f) for f in event.faces if f.get('face_epoch',0)>=self.min_face_epoch]
        people = self.store.people()
        person_ids = {p['id'] for p in people}
        if event.kind not in {'visual_arrival','conversation_lull','ambient'}: raise ValueError('UnknownOpportunity')
        if event.person_id and event.person_id not in person_ids: raise ValueError('VisualPersonNoLongerAvailable')
        if not faces: raise ValueError('VisualTargetStale')
        if event.kind=='visual_arrival' and not any(f['track_id']==event.target_track for f in faces):
            raise ValueError('VisualTargetStale')
        if event.kind=='conversation_lull' and (not self.current or event.source_turn_id!=self.current[0]):
            raise ValueError('StaleLullSource')
        cues=[cue.public() for cue in event.public_cues if self.observation_available(cue.public())]
        if event.scene_id and event.scene_images:
            scene={'cue_id':event.scene_id,'kind':'scene','source':'current camera snapshots',
                   'source_url':'','published_at':self.wall_clock(),'expires_at':self.wall_clock()+10,
                   'text':'Two ordered snapshots when available. Interpret only visible evidence; no verified identity, emotion or mouth-motion evidence.'}
            if self.observation_available(scene):cues.append(scene)
        from .memory_opportunities import candidates
        offered = candidates(self.store,faces,self.calendar_today(),self.memory_cursor)
        self.memory_cursor += len(offered)
        return {'observations':cues,'calendar_date':self.calendar_today().isoformat(),'memory_opportunities':offered,'trigger':event.kind,'event_id':event.event_id,'state_revision':self.store.revision,
                'participation':self.initiative.context(self.trace.clock()),
                'opportunity':{'kind':event.kind,'source_turn_id':event.source_turn_id,
                               'quiet_seconds':event.quiet_seconds,
                               'audience':'arrival_target' if event.kind=='visual_arrival' else 'visible_group'},
                **({'visual_arrival':{'face_track_id':event.target_track,'person_id':event.person_id,
                                     'returning_this_session':event.returning}} if event.kind=='visual_arrival' else {}),
                'speaker':{'person_id':None,'state':'unresolved','reason':'no_speech_observed'},
                'faces':faces,'people':people,'claims':[],
                'pending_question':self.pending,'frozen_evaluation':self.frozen}

    def execute_opportunity(self, call, context, cancel):
        """Visual cues can ask or stay quiet, never create identity or personal facts."""
        request_id = context['event_id']+':'+call.call_id
        try:
            cancel.check()
            validate(call.name,call.arguments)
            if self.store.revision!=context['state_revision']: raise ValueError('StaleStateRevision')
            if call.name=='use_observation':
                cue=next((r for r in context.get('observations',[]) if r['cue_id']==call.arguments['cue_id']),None)
                if cue is None or not self.observation_available(cue):raise ValueError('ObservationUnavailable')
                data={'text':call.arguments['text'],'cue':cue}
            elif call.name=='use_memory_opportunity':
                from .memory_opportunities import candidates
                offered={r['claim_id'] for r in context.get('memory_opportunities',[])}
                current=candidates(self.store,context['faces'],self.calendar_today(),limit=128)
                candidate=next((r for r in current if r['claim_id']==call.arguments['claim_id'] and r['claim_id'] in offered),None)
                if not candidate:raise ValueError('MemoryOpportunityUnavailable')
                data={'text':call.arguments['text'],'claim_id':candidate['claim_id'],'occasion':candidate['occasion']}
            elif call.name=='remain_silent': data={'reason':call.arguments['reason']}
            elif call.name=='ask_face_question' and context['trigger']=='visual_arrival':
                if call.arguments['face_track_id']!=context['visual_arrival']['face_track_id']:
                    raise ValueError('VisualQuestionTargetMismatch')
                data={'question':call.arguments['text'],'face_track_id':call.arguments['face_track_id'],'addressee_id':None}
            else: raise ValueError('VisualEventDoesNotAuthorizeMutation')
            return ToolResult(request_id,'ok','executed',self.store.revision,data)
        except ValueError as exc:
            return ToolResult(request_id,'rejected',str(exc),self.store.revision)

    def observation_available(self, cue):
        if cue['expires_at']<=self.wall_clock():return False
        provider=self.scene_observer if cue['kind']=='scene' else self.observations
        if provider is not None and not provider.valid(cue['cue_id']):return False
        return not self.store.rows('SELECT 1 FROM observation_delivery WHERE cue_id=? AND delivered_at>?',
                                   (cue['cue_id'],self.wall_clock()-86400))

    def observation_delivered(self, cue):
        with self.store.transaction() as conn:
            conn.execute('DELETE FROM observation_delivery WHERE delivered_at<?',(self.wall_clock()-7*86400,))
            conn.execute('INSERT OR REPLACE INTO observation_delivery VALUES(?,?,?)',
                         (cue['cue_id'],cue['kind'],self.wall_clock()))
            self.store.advance(conn)

    def memory_delivered(self, selected):
        with self.store.transaction() as conn:
            current=conn.execute("SELECT status FROM claims WHERE id=?",(selected['claim_id'],)).fetchone()
            if not current or current[0]!='accepted':return
            written=conn.execute('INSERT OR IGNORE INTO memory_followup_delivery VALUES(?,?,?)',
                         (selected['claim_id'],selected['occasion'],self.calendar_today().isoformat()))
            if written.rowcount:self.store.advance(conn)

    def execute(self, call, expected_revision, cancel):
        turn_id,capture,transcript,observation,attribution = self.current
        request_id = capture.session_id + ":" + call.call_id
        fingerprint = hashlib.sha256(json.dumps({"name":call.name,"args":call.arguments},sort_keys=True).encode()).hexdigest()
        revision = self.store.revision
        try:
            validate(call.name,call.arguments)
            if self.frozen and call.name not in {"recall","ask_question","ask_face_question","settle_question","remain_silent"}:
                raise ValueError("FrozenEvaluationDisallowsMutation")
            with self.store.transaction() as conn:
                cancel.check()
                previous = conn.execute("SELECT * FROM operations WHERE request_id=?",(request_id,)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ValueError("RequestIdReusedWithDifferentArguments")
                    data = json.loads(previous["result"])
                    return ToolResult(request_id,data["outcome"],data["reason"],revision,data.get("data",{}))
                revision = int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                if revision != expected_revision:
                    raise ValueError("StaleStateRevision")
                data, mutation = self._apply(conn,call.name,call.arguments)
                cancel.check()
                if mutation:
                    revision = self.store.advance(conn)
                result = ToolResult(request_id,"ok","executed",revision,data)
                conn.execute("INSERT INTO operations VALUES(?,?,?)",(request_id,fingerprint,json.dumps(asdict(result))))
            # In-memory proposals are applied only after the transaction commits.
            self._after(call.name,data)
            self.trace.emit("tool_result", name=call.name, **asdict(result))
            return result
        except ValueError as exc:
            result = ToolResult(request_id,"rejected",str(exc),self.store.revision)
        except Exception as exc:
            from .contracts import Cancelled
            if isinstance(exc,Cancelled):
                raise
            result = ToolResult(request_id,"failed",type(exc).__name__,self.store.revision)
        self.trace.emit("tool_result",name=call.name,**asdict(result))
        return result

    def _source_allowed(self, args, name):
        turn_id,capture,transcript,observation,attribution = self.current
        if args.get("source_turn_id",turn_id) != turn_id:
            raise ValueError("SourceTurnMismatch")
        if (not transcript.trusted or capture.echo_risk or capture.truncated or observation.mixed or capture.overlap is True
                or capture.parent_capture_id is not None):
            raise ValueError("UntrustedSource")
        if attribution.reason=="tentative_continuity" and (
                name in {"rename_person","forget_person"} or
                (name=="introduce_person" and args["subject"]=="self") or
                (name=="record_claim" and args["kind"]=="self_report")):
            raise ValueError("TentativeTrackDoesNotAuthorizeSelfIdentity")

    def _person(self, person_id):
        person = self.store.person(person_id)
        if person is None or person["status"]=="retracted":
            raise ValueError("UnknownPersonId")
        return person

    def _feedback(self, args):
        turn_id,capture,transcript,observation,attribution = self.current
        self._source_allowed(args,"conversation_feedback")
        if not attribution.track_id or attribution.reason == "tentative_continuity":
            raise ValueError("FeedbackNeedsCurrentSpeakerEvidence")
        participant = {"person_id":attribution.person_id,"track_id":attribution.track_id}
        action, reference, topic = args["action"], args["reference_id"], args["topic"]
        needs_reference = action in {"invitation_answered","invitation_declined","retract","floor_resolved"}
        if bool(reference) != needs_reference or bool(topic) != (action in {"topic_closed","topic_avoid"}):
            raise ValueError("FeedbackFieldsMismatch")
        if action == "yield_floor" and args["text"] is not None:
            raise ValueError("FloorYieldMustBeSilent")
        now = self.trace.clock()
        if action in {"retract","floor_resolved"}:
            previous = next((r for r in self.initiative.context(now)["feedback"] if r["id"]==reference), None)
            if not previous: raise ValueError("FeedbackReferenceMissing")
            if previous['source_turn_id']==turn_id:
                raise ValueError('FeedbackNeedsNewSource')
            if action=='floor_resolved':
                if previous['action']!='yield_floor': raise ValueError('NotAHumanFloorReference')
            else:
                owner = previous["participant"]
                if not (owner["person_id"] == participant["person_id"] if owner["person_id"]
                        else owner["track_id"] == participant["track_id"]):
                    raise ValueError("FeedbackBelongsToAnotherParticipant")
        elif needs_reference:
            previous = next((r for r in self.initiative.outcomes if r["event_id"]==reference), None)
            if not previous or not previous["heard_complete_phrases"]:
                raise ValueError("FeedbackNeedsAudibleOpportunity")
            if previous["at"] > now or now-previous["at"] > 300:
                raise ValueError("FeedbackOpportunityExpired")
            if turn_id == previous['source_turn_id'] or capture.started_at < previous['at']:
                raise ValueError("FeedbackSourcePrecedesOpportunity")
            target = previous["target_person_id"]
            if target and target != attribution.person_id:
                raise ValueError("FeedbackTargetMismatch")
        if action not in {'retract','floor_resolved'} and len(self.initiative.feedback)==32 and all(
                r['action']=='topic_avoid' for r in self.initiative.feedback):
            raise ValueError('FeedbackCapacityReached')
        return {"id":uuid4().hex,"action":action,"reference_id":reference,"topic":topic,
                "source_turn_id":turn_id,"source_text":transcript.text[:500],
                "participant":participant,"at":now,"expires_at":now+120 if action=="yield_floor" else None,
                "text":args["text"],"reason":"conversation_feedback"},True

    def _apply(self, conn, name, args):
        turn_id,capture,transcript,observation,attribution = self.current
        if name == "remain_silent":
            data = {"reason":args["reason"]}
            if not self.frozen and args['reason'] in {'other_addressee','background_conversation','unfinished_thought'}:
                try:
                    data['feedback'], _ = self._feedback(dict(action='yield_floor',reference_id=None,
                        topic=None,source_turn_id=turn_id,text=None))
                except ValueError:
                    # Silence remains valid even when source/track evidence cannot support a note.
                    pass
            return data,'feedback' in data
        if name == "conversation_feedback":
            return self._feedback(args)
        if name == "set_initiative":
            self._source_allowed(args,name)
            return {"mode":args["mode"],"source_turn_id":turn_id,
                    "reason":"explicit_initiative_request"},False
        if name == "remember_followup":
            self._source_allowed(args,name)
            if attribution.state!='supported' or not attribution.person_id:
                raise ValueError('FollowupNeedsSupportedSelfIdentity')
            from .memory_opportunities import validate_date
            validate_date(args['kind'],args['on_date'])
            data, _ = self._apply(conn,'record_claim',dict(subject_id=attribution.person_id,
                subject_reference=None,predicate=args['predicate'],value=args['value'],
                kind='self_report',source_turn_id=turn_id))
            if data['status']!='accepted':raise ValueError('FollowupNeedsAcceptedClaim')
            conn.execute('INSERT INTO memory_followups VALUES(?,?,?,?)',
                         (data['claim_id'],args['kind'],args['on_date'],args['audience']))
            return {**data,'followup_kind':args['kind'],'on_date':args['on_date'],'audience':args['audience']},True
        if name == "recall":
            if args["subject_id"]:
                self._person(args["subject_id"])
            return {"claims":self.store.claims(args["subject_id"])},False
        if name == "ask_face_question":
            if not any(f["track_id"]==args["face_track_id"] for f in self.faces):
                raise ValueError("FaceEvidenceMissing")
            return {"question":args["text"],"addressee_id":None,"face_track_id":args["face_track_id"]},False
        if name == "ask_question":
            if args["addressee_id"]:
                self._person(args["addressee_id"])
            return {"question":args["text"],"addressee_id":args["addressee_id"]},False
        if name == "settle_question":
            if not self.pending:
                raise ValueError("NoDeliveredPendingQuestion")
            return {"status":args["status"]},False
        self._source_allowed(args,name)
        if name == "reset_face_identity":
            person = self._person(args["person_id"])
            face = next((f for f in self.faces if f["track_id"]==args["face_track_id"]),None)
            if face is None or person['id'] not in {face.get('identity'),self.face_associations.get(face['track_id'])}:
                raise ValueError("FaceCorrectionNeedsCurrentAssociation")
            conn.execute("DELETE FROM face_references WHERE person_id=?", (person['id'],))
            conn.execute("INSERT OR IGNORE INTO face_learning_blocks VALUES(?)", (person['id'],))
            return {"person_id":person['id'],"status":"face_references_removed"},True
        if name == "introduce_person":
            if args["face_track_id"] and not any(f["track_id"]==args["face_track_id"] for f in self.faces):
                raise ValueError("FaceEvidenceMissing")
            name_text = args["name"].strip()
            if len(name_text) > 120 or any(ord(c)<32 for c in name_text):
                raise ValueError("InvalidDisplayLabel")
            matches = self.store.resolve(name_text)
            person_id = args["person_id"]
            if person_id:
                self._person(person_id)
            elif len(matches)==1:
                person_id = matches[0]["id"]
            elif len(matches)>1:
                raise ValueError("AmbiguousName")
            if args["subject"] == "self":
                if not attribution.track_id or observation.embedding is None:
                    raise ValueError("IntroductionNeedsUsableVoiceTrack")
                track = self.tracker.tracks[attribution.track_id]
                if track.person_id and track.person_id != person_id:
                    raise ValueError("ConflictingVoiceIdentity")
                if attribution.person_id and attribution.person_id != person_id:
                    raise ValueError("NameCannotOverrideAcoustics")
                if person_id:
                    if self.store.person(person_id)["status"]=="suspect":
                        raise ValueError("SuspectProfileRequiresLocalReviewOrVoiceReset")
                    if person_id in self.store.profiles(self.tracker.namespace) and attribution.person_id!=person_id:
                        raise ValueError("ExistingProfileNeedsCompatibleVoice")
                    if any(e.person_id==person_id and e.track_id!=attribution.track_id for e in self.enrollments.values()):
                        raise ValueError("CompetingVoiceAssociation")
            if not person_id:
                person_id = self.store.add_person(conn,name_text,args["aliases"],turn_id)
            from .face_attribution import target_allowed, learning_samples
            face = next((f for f in self.faces if f["track_id"]==args["face_track_id"]),None)
            # The semantic owner may omit the face ID for an ordinary self-introduction.
            if face is None and args['subject']=='self' and len(self.faces)==1:
                face = self.faces[0]
            competing = any(key!=attribution.track_id and 0<=capture.ended_at-track.last_at<=8
                            for key,track in self.tracker.tracks.items())
            consistent_intro = (not observation.direction_conflict and observation.consistency is not None and
                                observation.consistency>=self.tracker.policy.track_threshold)
            allowed = face is not None and target_allowed(face,self.faces,args['subject'],attribution,self.pending,
                                                          competing,introduction_source=consistent_intro)
            status, saved = 'no_face_target', 0
            if face is not None:
                status = 'ambiguous_face_target'
                if face.get('identity') not in (None,person_id):
                    allowed, status = False, 'conflicting_face_identity'
                if allowed:
                    conn.execute('DELETE FROM face_learning_blocks WHERE person_id=?', (person_id,))
                    samples,status = learning_samples(face,capture,person_id,self.store)
                    if samples:
                        saved = self.store.save_faces(conn,person_id,samples,turn_id,
                            {'subject':args['subject'],'said_by':attribution.person_id,'source_track':attribution.track_id,
                             'face_track_id':face['track_id']})
                        status = 'saved' if saved else 'already_saved'
            return {"person_id":person_id,"subject":args["subject"],"track_id":attribution.track_id if args["subject"]=="self" else None,
                    "status":"hypothesis","face_track_id":face['track_id'] if face else None,
                    "face_status":status,"face_samples_saved":saved,"face_target_allowed":allowed},True
        if name == "record_claim":
            self._person(args["subject_id"])
            if args["kind"]=="third_party":
                reference = args["subject_reference"]
                matches = self.store.resolve(reference) if reference else []
                if not reference or label(reference) not in label(transcript.text) or len(matches)!=1 or matches[0]["id"]!=args["subject_id"]:
                    raise ValueError("ThirdPartySubjectNeedsUniqueExplicitReference")
            elif args["subject_reference"] is not None:
                raise ValueError("SelfReportDoesNotTakeNameReference")
            if args["kind"]=="self_report" and attribution.person_id is None:
                track = self.tracker.tracks.get(attribution.track_id)
                if not track or track.person_id!=args["subject_id"]:
                    raise ValueError("UnresolvedSelfReportSubject")
            if args["kind"] == "self_report" and attribution.person_id not in (None,args["subject_id"]):
                raise ValueError("SelfReportSubjectMismatch")
            supported = attribution.state == "supported" and attribution.person_id is not None
            status = "accepted" if supported else "candidate"
            if conn.execute("SELECT 1 FROM claims WHERE subject_id=? AND predicate=? AND status='accepted' AND value<>?",
                            (args["subject_id"],args["predicate"],args["value"])).fetchone():
                status = "candidate"  # A conflicting new claim never silently replaces the current one.
            claim_id = uuid4().hex
            conn.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?)",(claim_id,args["subject_id"],args["predicate"],args["value"],
                         attribution.person_id,attribution.track_id,turn_id,args["kind"],status,None,self.store.revision+1))
            return {"claim_id":claim_id,"status":status,"subject_id":args["subject_id"],"said_by":attribution.person_id,
                    "subject_name":self.store.person(args["subject_id"])["name"],"predicate":args["predicate"],"value":args["value"]},True
        if name in {"correct_claim","retract_claim"}:
            claim = conn.execute("SELECT * FROM claims WHERE id=?",(args["claim_id"],)).fetchone()
            if not claim or claim["status"] not in {"accepted","candidate"}:
                raise ValueError("UnknownOrRetiredClaim")
            if not attribution.person_id or attribution.person_id not in (claim["said_by"],claim["subject_id"]):
                raise ValueError("CorrectionNeedsSupportedSourceOrSubject")
            conn.execute("UPDATE claims SET status=? WHERE id=?",("superseded" if name=="correct_claim" else "retracted",claim["id"]))
            if name == "retract_claim":
                return {"claim_id":claim["id"],"status":"retracted"},True
            claim_id = uuid4().hex
            kind = "self_report" if attribution.person_id==claim["subject_id"] else "third_party"
            conn.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?)",(claim_id,claim["subject_id"],claim["predicate"],args["value"],
                         attribution.person_id,attribution.track_id,turn_id,kind,"accepted",claim["id"],self.store.revision+1))
            return {"claim_id":claim_id,"status":"accepted","supersedes":claim["id"],"subject_id":claim["subject_id"],
                    "subject_name":self.store.person(claim["subject_id"])["name"],"predicate":claim["predicate"],"value":args["value"]},True
        if name == "cancel_association":
            if not args["person_id"] and not args["track_id"]:
                raise ValueError("AssociationTargetRequired")
            matches = [e for e in self.enrollments.values() if
                       (not args["person_id"] or e.person_id==args["person_id"]) and
                       (not args["track_id"] or e.track_id==args["track_id"])]
            if not matches:
                raise ValueError("NoMatchingProvisionalAssociation")
            person_id = matches[0].person_id
            retired = self.store.person(person_id)["status"]=="provisional"
            if retired:
                conn.execute("UPDATE people SET status='retracted' WHERE id=?",(person_id,))
                conn.execute("DELETE FROM face_references WHERE person_id=?", (person_id,))
                conn.execute("UPDATE claims SET status='retracted' WHERE subject_id=? AND status='candidate'",(person_id,))
            conn.execute("DELETE FROM voice_candidates WHERE person_id=?", (person_id,))
            return {"person_id":person_id,"track_id":args["track_id"],"status":"cancelled"},True
        if name in {"rename_person","forget_person"}:
            person = self._person(args["person_id"])
            if person["id"] not in self.identity_targets(name):
                raise ValueError("SupportedSelfIdentityRequired")
            if name=="forget_person":
                self.store.forget(conn,person["id"])
                return {"person_id":person["id"],"status":"forgotten"},True
            if len(args["name"].strip())>120 or any(ord(c)<32 for c in args["name"]):
                raise ValueError("InvalidDisplayLabel")
            conn.execute("UPDATE people SET name=?,aliases=? WHERE id=?",(args["name"],json.dumps(args["aliases"]),person["id"]))
            return {"person_id":person["id"],"status":"renamed"},True
        raise ValueError("UnknownTool")

    def _after(self,name,data):
        if name=='remain_silent' and 'feedback' in data:
            self._after('conversation_feedback',data['feedback'])
        if name=='conversation_feedback':
            if data['action'] in {'retract','floor_resolved'}:
                self.initiative.feedback = deque((r for r in self.initiative.feedback
                    if r['id'] != data['reference_id']), maxlen=32)
            else:
                # Proposed reply text is not evidence of actual delivery.
                if len(self.initiative.feedback)==32:
                    # Active explicit boundaries cannot disappear behind routine feedback.
                    expendable = next(r for r in self.initiative.feedback if r['action']!='topic_avoid')
                    self.initiative.feedback.remove(expendable)
                self.initiative.feedback.append({k:v for k,v in data.items() if k not in {'text','reason'}})
        if name=='set_initiative':
            self.initiative.quiet = data['mode']=='quiet'
            self.initiative.policy_source = data['source_turn_id']
        if name=="introduce_person" and data.get("face_track_id"):
            key = data['face_track_id']
            self.face_associations[key] = data["person_id"]
            if data.get('face_target_allowed') and data['face_status']=='need_clear_compatible_views':
                self.face_learning[key] = {'person_id':data['person_id'],'source_turn':self.current[0],
                    'subject':data['subject'],'said_by':self.current[4].person_id,
                    'source_track':self.current[4].track_id,'expires':self.current[1].ended_at+30}
            else:
                self.face_learning.pop(key,None)
            if data.get('face_samples_saved'):
                self.trace.emit('face_learning_saved',person_id=data['person_id'],samples=data['face_samples_saved'],revision=self.store.revision)
        if name in {"forget_person","cancel_association","reset_face_identity"}:
            self.face_learning = {k:v for k,v in self.face_learning.items() if v["person_id"]!=data.get("person_id")}
            if self.face_invalidator:
                self.min_face_epoch = self.face_invalidator()
                self.face_learning.clear()
                self.face_associations.clear()
            self.faces = ()
            self.face_associations = {k:v for k,v in self.face_associations.items() if v!=data.get("person_id")}
        turn_id,capture,transcript,observation,attribution = self.current
        if name=="introduce_person" and data["subject"]=="self":
            self.tracker.associate(attribution.track_id,data["person_id"],attribution.person_id==data["person_id"])
            if attribution.person_id is None and attribution.track_id not in self.enrollments:
                proposal = self.evidence.get(attribution.track_id)
                if proposal is None:
                    proposal = Enrollment(None,attribution.track_id,observation.embedding)
                # The introduction must align with each retained sample, not just recency.
                proposal.samples = [s for s in proposal.samples if cosine(s[2].embedding, observation.embedding) >= .55]
                proposal.digests = {hashlib.sha256(s[0].pcm).hexdigest() for s in proposal.samples}
                proposal.person_id = data["person_id"]
                proposal.anchor = observation.embedding
                proposal.updated_at = time.time()
                self.evidence[attribution.track_id] = proposal
                self.enrollments[attribution.track_id] = proposal
                self.persist_evidence(proposal)
                self.learn_observation()
        if name=='cancel_association':
            self.initiative.discard_association(data.get('person_id'),data.get('track_id'))
            for key, proposal in self.enrollments.items():
                if proposal.person_id==data.get('person_id'):
                    self.initiative.discard_association(None,key)
        if name in {"cancel_association","forget_person"}:
            for key,e in list(self.enrollments.items()):
                if data.get("person_id")==e.person_id or data.get("track_id")==key:
                    del self.enrollments[key]
                    self.evidence.pop(key, None)
                    with self.store.transaction() as conn:
                        conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (key,))
                    self.tracker.forget(e.person_id)
        if name=="forget_person":
            self.initiative.forget()
            self.tracker.forget(data["person_id"])
            self.pending = None
            self.forgotten = True
            from dataclasses import replace
            from .speakers import VoiceObservation,Attribution
            self.current = (turn_id,replace(capture,pcm=b""),transcript,
                            VoiceObservation(observation.namespace),Attribution(reason="person_forgotten"))
        if name in {"introduce_person","cancel_association","rename_person","forget_person","reset_face_identity"}:
            self.trace.emit("association_result",source_turn_id=turn_id,revision=self.store.revision,operation=name,**data)
        if name=="settle_question":
            self.trace.emit("question_settled",source_turn_id=turn_id,**data)
        if name in {"settle_question","introduce_person","record_claim","correct_claim","cancel_association"}:
            self.pending = None

    def learn_faces(self):
        if self.frozen: return
        from .face_attribution import learning_samples
        turn,capture,transcript,observation,attribution = self.current
        visible = {f['track_id']:f for f in self.faces}
        self.face_learning = {k:v for k,v in self.face_learning.items() if k in visible and capture.ended_at<=v['expires']}
        if not transcript.trusted or capture.echo_risk or observation.mixed or capture.overlap is True or capture.truncated or capture.parent_capture_id:
            return
        # A supported voice and sole clear face can associate an already named person
        # without requiring another introduction. Competing recent voices veto this.
        if attribution.person_id and len(self.faces)==1:
            from .face_attribution import target_allowed
            face = self.faces[0]
            competing = any(k!=attribution.track_id and 0<=capture.ended_at-t.last_at<=8 for k,t in self.tracker.tracks.items())
            samples = face.get('_samples',())
            blocked = self.store.rows('SELECT person_id FROM face_learning_blocks WHERE person_id=?', (attribution.person_id,)) if samples else []
            if (samples and not blocked and face.get('identity') in (None,attribution.person_id) and
                target_allowed(face,self.faces,'self',attribution,None,competing) and
                not self.store.face_profiles(samples[0].namespace).get(attribution.person_id)):
                self.face_learning.setdefault(face['track_id'], {'person_id':attribution.person_id,
                    'source_turn':turn,'subject':'self','said_by':attribution.person_id,
                    'source_track':attribution.track_id,'expires':capture.ended_at+30})
        for key,proposal in list(self.face_learning.items()):
            if proposal['subject']=='self' and attribution.track_id!=proposal['source_track']: continue
            samples,reason = learning_samples(visible[key],capture,proposal['person_id'],self.store)
            if not samples:
                if reason!='need_clear_compatible_views': self.face_learning.pop(key,None)
                continue
            try:
                with self.store.transaction() as conn:
                    saved = self.store.save_faces(conn,proposal['person_id'],samples,proposal['source_turn'],proposal)
                    revision = self.store.advance(conn) if saved else self.store.revision
                self.face_learning.pop(key,None)
                self.trace.emit('face_learning_saved',person_id=proposal['person_id'],samples=saved,revision=revision)
            except Exception as exc:
                self.trace.emit('face_learning_failed',error_type=type(exc).__name__)

    def learn_observation(self):
        # A storage failure must not discard the user's words or kill listening.
        try:
            self.collect_evidence()
            self.advance_enrollment()
        except Exception as exc:
            self.trace.emit("voice_learning_failed", error_type=type(exc).__name__)

    def persist_evidence(self, proposal):
        with self.store.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO voice_candidates VALUES(?,?,?,?,?)",
                (proposal.track_id, proposal.person_id, self.tracker.namespace, proposal.updated_at, json.dumps(proposal.encode())))
            if proposal.person_id:
                conn.executemany("UPDATE artifacts SET person_id=? WHERE capture_id=? AND person_id IS NULL",
                    [(proposal.person_id, sample[0].capture_id) for sample in proposal.samples])
            conn.execute("DELETE FROM voice_candidates WHERE track_id NOT IN (SELECT track_id FROM voice_candidates ORDER BY updated_at DESC LIMIT 12)")

    def collect_evidence(self):
        if self.frozen:
            return
        turn, capture, transcript, observation, attribution = self.current
        self.evidence = {k:v for k,v in self.evidence.items() if k in self.tracker.tracks}
        if not attribution.learning_eligible or attribution.person_id:
            if not attribution.person_id:
                self.trace.emit('voice_learning_deferred',capture_id=capture.capture_id,
                    reason=attribution.reason if not attribution.track_id else 'sample_quality',
                    voiced_seconds=observation.voiced_seconds,consistency=observation.consistency)
            return
        proposal = self.evidence.get(attribution.track_id)
        if proposal is None:
            proposal = Enrollment(None, attribution.track_id, observation.embedding)
        if proposal.add(capture, transcript, observation, attribution, turn):
            self.evidence[attribution.track_id] = proposal
            self.persist_evidence(proposal)
        else:
            self.trace.emit('voice_learning_deferred',capture_id=capture.capture_id,
                            reason=proposal.last_reason)

    def advance_enrollment(self):
        if self.frozen:
            return
        turn_id,capture,transcript,observation,attribution = self.current
        proposal = self.enrollments.get(attribution.track_id)
        if proposal:
            self.trace.emit("voice_learning_progress",person_id=proposal.person_id,track_id=proposal.track_id,
                qualifying_turns=len(proposal.samples),voiced_seconds=sum(s[2].voiced_seconds for s in proposal.samples),
                reason=proposal.last_reason)
            if proposal.ready:
                try:
                    self._source_allowed({}, "rename_person")
                except ValueError:
                    return
                # Recheck earlier samples against voices discovered later in the exchange.
                rivals = {key: track.references for key, track in self.tracker.tracks.items()
                          if key != proposal.track_id}
                for sample in proposal.samples:
                    vectors = [sample[2].embedding, *(w[2] for w in sample[2].windows)]
                    for vector in vectors:
                        own = cosine(vector, proposal.anchor)
                        ranked = self.tracker.rank(vector, rivals)
                        if ranked and own - ranked[0][1] < self.tracker.policy.margin:
                            self.trace.emit("voice_learning_deferred", person_id=proposal.person_id,
                                            reason="competing_group_evidence")
                            return
                with self.store.transaction() as conn:
                    self.store.save_voice(conn,proposal.person_id,proposal.samples,observation.namespace)
                    conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (proposal.track_id,))
                    revision = self.store.advance(conn)
                self.tracker.associate(proposal.track_id,proposal.person_id,True)
                del self.enrollments[proposal.track_id]
                self.evidence.pop(proposal.track_id, None)
                self.trace.emit("voice_learning_saved",person_id=proposal.person_id,samples=len(proposal.samples),revision=revision)

    def question_delivered(self,text,addressee_id,completed_at,face_track_id=None):
        self.pending = {"text":text,"addressee_id":addressee_id,"delivered_at":completed_at}
        if face_track_id: self.pending["face_track_id"] = face_track_id
        self.trace.emit("question_delivered",**self.pending)
