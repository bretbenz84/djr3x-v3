"""Scripted decisions through production Session/runtime; not a semantic-quality eval."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
from r3x.contracts import ToolCall,ProviderFailure
from r3x.output import OutputOwner,TextOutput
from r3x.scenario import SyntheticTTS,SyntheticSink
from r3x.session import Session
from r3x.trace import report
from tests import test_memory_core,test_proactive_core,test_conversation_intent,test_agency


def update(context,action='open',scope='general'):
    offered=context['invitations']
    return {'source_id':offered['source_id'],'revision':offered['revision'],
            'invitation_id':None if action=='open' else offered['pending']['id'],
            'action':action,'topic':'A rhythm guessing game' if action=='open' else None,
            'scope':scope if action=='open' else None}


def offer(context,**changes):
    args={'text':'Want to invent a rhythm with only three beats?', 'intent_update':None,
          'next_opportunity':test_agency.next_request(context,'after_speech',3),
          'invitation_update':update(context)}
    args.update(changes)
    return ToolCall(uuid4().hex,'respond_with_intent',args)


def close(context,action='lapsed',text=None,schedule=False):
    return ToolCall(uuid4().hex,'remain_silent' if text is None else 'respond_with_intent',{
        **({'reason':'no_response_needed'} if text is None else {'text':text}),
        'intent_update':None,'invitation_update':update(context,action),
        'next_opportunity':test_agency.next_request(context) if schedule else None})


class InvitationTests(test_memory_core.MemoryFixture):
    turn=test_proactive_core.InitiativeCoreTests.turn
    tick=test_agency.AgencyTests.tick

    def setup_core(self,choose=offer):
        self.brain=test_conversation_intent.IntentBrain(choose)
        test_proactive_core.InitiativeCoreTests.setup_core(self,self.brain,
            OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace))
        self.runtime.camera=None
        self.addCleanup(self.session.close)

    @property
    def invitations(self):return self.state.initiative.invitations

    def reasons(self):return [r.get('reason') for r in self.trace.rows if r['event']=='invitation_rejected']

    def test_audible_offer_waits_only_until_selected_review_and_lapses_explicitly(self):
        self.setup_core();result=self.tick(100)
        self.assertEqual(result.status,'completed');self.assertEqual(self.brain.calls,1)
        row=deepcopy(self.invitations.pending)
        self.assertEqual(row['review_at'],103)
        self.assertIsNone(self.tick(102));self.assertEqual(self.brain.calls,1)
        self.brain.choose=lambda c:close(c,schedule=True)
        self.assertEqual(self.tick(108).status,'suppressed')
        self.assertIsNone(self.invitations.pending)
        self.assertEqual(self.invitations.recent[-1]['status'],'lapsed')
        self.assertEqual(self.invitations.recent[-1]['heard_text'],row['heard_text'])
        self.brain.choose=lambda c:ToolCall('idea','respond_with_intent',{'text':'A waltz with a missing beat would keep the dancers awake.','intent_update':None})
        self.assertEqual(self.tick(116).status,'completed')
        self.assertEqual(self.brain.calls,3)
        self.assertEqual(self.store.claims(),[]);self.assertEqual(self.store.people(),[])

    def test_time_and_new_speech_never_infer_answer_decline_or_lapse(self):
        self.setup_core();self.tick(100)
        self.trace.at=200
        self.assertTrue(self.invitations.context({},200)['review_due'])
        self.assertEqual(self.invitations.pending['status'],'waiting')
        self.session.note_speech(200)
        self.assertEqual(self.invitations.pending['status'],'waiting')
        self.brain.choose=lambda c:'That is a different topic.'
        self.turn('The clock is wrong.')
        self.assertEqual(self.invitations.pending['status'],'waiting')

    def test_human_answer_decline_and_redirection_are_explicit_in_same_reply(self):
        for action,text in (('answered','Sure, three beats.'),('declined','No thanks.'),('superseded','Instead, tell me about jazz.')):
            with self.subTest(action=action):
                self.setup_core();self.trace.at+=10;self.turn('Offer me a game.')
                self.brain.choose=lambda c:close(c,action,'Understood.')
                self.trace.at+=4
                result=self.turn(text)
                self.assertEqual(result.status,'completed')
                self.assertEqual(self.invitations.recent[-1]['status'],action)
                self.assertIsNone(self.invitations.pending)
                self.assertEqual(self.brain.calls,2)

    def test_proactive_cannot_claim_human_answer_or_decline(self):
        self.setup_core();self.tick(100)
        self.brain.choose=lambda c:close(c,'declined')
        self.tick(108)
        self.assertEqual(self.invitations.pending['status'],'waiting')
        self.assertIn('InvitationNeedsHumanSource',self.reasons())

    def test_unheard_failed_and_text_only_offers_do_not_create_pending_state(self):
        self.setup_core()
        def fail(*args):raise ProviderFailure('SyntheticPlaybackFailure')
        self.session.output.sink.play=fail
        self.assertEqual(self.tick(100).status,'failed')
        self.assertIsNone(self.invitations.pending)
        self.trace.at=110;self.session.output=TextOutput(self.trace)
        self.turn('Tell me your idea.')
        self.assertIsNone(self.invitations.pending)

    def test_cancelled_offer_cannot_rearm_or_commit_after_audio_started(self):
        self.setup_core();original=self.session.output.sink.play
        def cancel(*args):
            result=original(*args);self.session.cancel();return result
        self.session.output.sink.play=cancel
        self.tick(100)
        self.assertIsNone(self.invitations.pending)

    def test_stale_malformed_or_unoffered_updates_preserve_reply_without_mutation(self):
        self.setup_core();self.tick(100)
        for mutate in (lambda v:{**v,'revision':v['revision']-1},lambda v:{**v,'source_id':'wrong'},
                       lambda v:{**v,'invitation_id':'invented'},lambda v:'invalid'):
            self.trace.at+=10
            self.brain.choose=lambda c:ToolCall(uuid4().hex,'respond_with_intent',{'text':'A different thought.',
                'intent_update':None,'invitation_update':mutate(update(c,'superseded'))})
            self.assertEqual(self.turn('A different subject.').status,'completed')
            self.assertEqual(self.invitations.pending['status'],'waiting')
        self.assertEqual(len(self.reasons()),4)

    def test_early_lapse_is_rejected_without_inventing_rejection(self):
        self.setup_core();self.tick(100)
        self.trace.at=101;self.brain.choose=lambda c:close(c)
        self.turn('Mm.')
        self.assertIn('InvitationReviewNotDue',self.reasons())
        self.assertEqual(self.invitations.pending['status'],'waiting')

    def test_open_requires_explicit_delivery_based_review(self):
        self.setup_core(lambda c:offer(c,next_opportunity=None))
        self.assertEqual(self.tick(100).status,'completed')
        self.assertIn('InvitationRequiresScheduledReview',self.reasons())
        self.assertIsNone(self.invitations.pending)

    def test_boundary_and_frozen_evaluation_cannot_be_bypassed(self):
        self.setup_core();self.tick(100)
        self.state.initiative.feedback.append({'action':'yield_floor','expires_at':200})
        self.assertIsNone(self.tick(104));self.assertEqual(self.brain.calls,1)
        self.state.initiative.feedback.clear()
        self.brain.choose=lambda c:ToolCall('quiet','set_initiative',{'mode':'quiet','source_turn_id':c['source_turn_id']})
        self.turn('Stop starting conversations.')
        self.assertIsNone(self.invitations.pending)
        self.assertEqual(self.invitations.recent[-1]['status'],'interrupted')
        self.assertIsNone(self.tick(200))

    def test_audience_scope_is_withheld_and_general_scope_survives_head_turn(self):
        self.setup_core(lambda c:offer(c,invitation_update=update(c,scope='audience')))
        self.turn('Ask me something.')
        self.assertIsNotNone(self.invitations.pending)  # Current acoustic track, no required camera.
        self.assertIsNone(self.invitations.context({'faces':[]},104)['pending'])
        self.assertIsNone(self.invitations.context({'faces':[{'track_id':'other','identity':None}]},104)['pending'])
        self.brain.choose=offer;self.trace.at=105;self.turn('A general game instead.')
        self.assertIsNotNone(self.invitations.context({'faces':[]},106)['pending'])
        self.assertEqual(self.invitations.context({'faces':[]},106)['recent'],[])

    def test_restart_forget_and_correction_erase_dependent_work(self):
        self.setup_core();self.tick(100)
        self.state.initiative.cancel_schedule('correct_claim')
        self.assertIsNone(self.invitations.pending);self.assertFalse(self.invitations.recent)
        self.trace.at=110;self.turn('Try another idea.')
        self.state.initiative.forget()
        self.assertIsNone(self.invitations.pending);self.assertFalse(self.invitations.recent)
        self.trace.at=120;self.turn('A new idea.')
        self.session.close()
        self.session=Session('restart',self.brain,OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace),self.trace,self.state)
        self.assertIsNone(self.invitations.pending);self.assertFalse(self.invitations.recent)

    def test_recent_invitations_are_bounded_and_new_offer_explicitly_supersedes(self):
        self.setup_core()
        for n in range(10):
            self.trace.at=100+n*4
            self.turn('Another invitation, please.')
        self.assertEqual(len(self.invitations.recent),6)
        self.assertTrue(all(r['status']=='superseded' for r in self.invitations.recent))
        self.assertEqual(self.invitations.pending['status'],'waiting')


    def test_scheduled_general_review_does_not_consume_pending_identity_question(self):
        from tests.test_proactive import event
        self.setup_core(lambda c:ToolCall('name','ask_face_question',{
            'text':'What is your name?', 'face_track_id':c['faces'][0]['track_id'],
            'next_opportunity':test_agency.next_request(c,'after_speech',3)}))
        self.assertEqual(self.session.process_opportunity(event(),0,lambda:True).status,'completed')
        pending=deepcopy(self.state.pending)
        self.brain.choose=lambda c:ToolCall('quiet-look','remain_silent',{'reason':'no_response_needed'})
        self.assertEqual(self.tick(108).status,'suppressed')
        self.assertEqual(self.brain.calls,2)
        self.assertEqual(self.state.pending,pending)
        self.assertEqual(self.store.people(),[])
        self.assertEqual(self.store.claims(),[])

    def test_acknowledgment_failure_does_not_erase_explicit_human_decline(self):
        self.setup_core();self.tick(100)
        self.brain.choose=lambda c:close(c,'declined','Understood.')
        def fail(*args):raise ProviderFailure('SyntheticPlaybackFailure')
        self.session.output.sink.play=fail
        self.trace.at=104
        self.assertEqual(self.turn('No thanks.').status,'failed')
        self.assertIsNone(self.invitations.pending)
        self.assertEqual(self.invitations.recent[-1]['status'],'declined')
        self.assertNotIn('Understood.',self.invitations.recent[-1]['heard_text'])

    def test_private_visual_memory_cannot_be_reclassified_as_general_invitation(self):
        from r3x import visual_memory
        self.setup_core()
        visual_memory.save(self.store,'session','[]',
            {'request_id':'private','status':'observed','stage':'complete','frame_id':'f'},
            {'observation_id':'private','description':'Private description','uncertainty':'Unknown'},'synthetic',100)
        self.brain.choose=lambda c:ToolCall('memory','use_visual_memory',{
            'observation_id':'private','text':'Want a game?', 'invitation_update':update(c),
            'next_opportunity':test_agency.next_request(c,'after_speech',3)})
        self.assertEqual(self.turn('Ask me something.').status,'completed')
        self.assertIsNone(self.invitations.pending)
        self.assertIn('GeneralInvitationCannotUsePrivateVisualMemory',self.reasons())

    def test_report_counts_lifecycle_without_claiming_quality_or_missing_latency(self):
        self.setup_core();self.tick(100);self.brain.choose=lambda c:close(c);self.tick(108)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl';path.write_text('\n'.join(json.dumps(r) for r in self.trace.rows))
            result=report(path)
        outcomes={r['outcome']:r['count'] for r in result['invitation_outcomes']}
        self.assertEqual(outcomes['waiting'],1);self.assertEqual(outcomes['lapsed'],1)
        self.assertNotIn('social_quality',result)
