from dataclasses import replace
from r3x.contracts import Capture,Transcript,ToolCall,ProviderFailure
from r3x.inspection import Inspection,MovementReceipt,InspectionFrame
from r3x.scene import SceneImage
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink,SyntheticTTS
from r3x.session import Session
from tests.test_memory_core import MemoryFixture


class Brain:
    def __init__(self):self.snapshots=[]
    def reply(self,snapshot,cancel):
        self.snapshots.append(snapshot)
        if not snapshot.continuation:
            yield ToolCall('inspect','inspect_view',{'direction':'behind','question':'What is behind you?'})
        else:
            yield 'The synthetic view shows a red square.' if snapshot.images else 'I could not obtain that view.'


class InspectionTests(MemoryFixture):
    def setup_session(self):
        self.events=[]
        def move(request,cancel,deadline):
            self.events.append('move');self.state.trace.at+=1
            return MovementReceipt(request.request_id,request.direction,request.view_epoch,self.state.trace.at)
        def wait(at,cancel,deadline):
            self.events.append('settle');self.state.trace.at=at
        def capture(request,cancel,deadline):
            self.events.append('capture');self.state.trace.at+=.01
            return InspectionFrame(request.request_id,request.direction,request.view_epoch,self.state.trace.at,
                SceneImage('synthetic-frame',self.state.trace.at,'data:image/png;base64,synthetic'))
        self.adapter=Inspection(self.state.trace.clock,move,wait,capture);self.adapter.start()
        self.brain=Brain()
        self.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace,inspection=self.adapter)
        self.session=Session('session',self.brain,self.output,self.state.trace,self.state)
        self.addCleanup(self.session.close)

    def turn(self):
        return self.session.process_turn(Capture('c','session',bytes(1024),98,99,99.6),
            Transcript('Look behind you and tell me what you see.',-.01,True),self.session.revision)

    def test_ordered_inspection_returns_image_only_after_settling(self):
        self.setup_session();result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.events,['move','settle','capture'])
        self.assertEqual(len(self.brain.snapshots),2)
        snapshot=self.brain.snapshots[-1]
        self.assertEqual(len(snapshot.images),1)
        data=snapshot.context['inspection']
        self.assertGreater(data['captured_at'],data['settled_at'])
        self.assertGreater(data['settled_at'],data['motion_completed_at'])
        self.assertFalse(snapshot.tools_enabled)
        self.assertNotIn('data:image',str(self.session.history_messages()))
        self.assertNotIn('data:image',str(self.state.trace.rows))
        self.assertFalse(self.store.claims())

    def test_blocked_move_never_captures(self):
        self.setup_session()
        self.adapter.move=lambda r,c,d:MovementReceipt(r.request_id,r.direction,r.view_epoch,100,'blocked')
        self.assertEqual(self.turn().status,'completed')
        self.assertEqual(self.events,[])
        self.assertFalse(self.brain.snapshots[-1].images)
        self.assertEqual(self.brain.snapshots[-1].context['inspection']['reason'],'MovementNotCompleted')

    def test_early_wait_return_does_not_capture(self):
        self.setup_session();self.adapter.wait_until=lambda *args:None
        self.turn();self.assertEqual(self.events,['move'])
        self.assertEqual(self.brain.snapshots[-1].context['inspection']['reason'],'MovementNotSettled')

    def test_old_or_wrong_direction_images_are_rejected(self):
        for wrong in ('old','direction','epoch'):
            with self.subTest(wrong=wrong):
                self.setup_session();original=self.adapter.capture
                def capture(r,c,d):
                    frame=original(r,c,d)
                    return replace(frame,**({'captured_at':100} if wrong=='old' else
                        {'direction':'current'} if wrong=='direction' else {'view_epoch':-1}))
                self.adapter.capture=capture;self.turn()
                self.assertFalse(self.brain.snapshots[-1].images)
                self.session.close()

    def test_cancel_while_settling_prevents_capture_and_continuation(self):
        self.setup_session()
        self.adapter.wait_until=lambda *args:self.session.cancel()
        self.assertEqual(self.turn().status,'interrupted')
        self.assertEqual(self.events,['move'])
        self.assertEqual(len(self.brain.snapshots),1)

    def test_capture_failure_retains_successful_motion_receipt(self):
        self.setup_session()
        def fail(*args):raise ProviderFailure('SyntheticCameraFailure')
        self.adapter.capture=fail;self.turn()
        data=self.brain.snapshots[-1].context['inspection']
        self.assertEqual(data['motion_status'],'simulated')
        self.assertEqual(data['reason'],'SyntheticCameraFailure')
        self.assertFalse(self.brain.snapshots[-1].images)

    def test_deadline_prevents_late_capture(self):
        self.setup_session()
        self.adapter.wait_until=lambda *args:setattr(self.state.trace,'at',116)
        self.turn();self.assertEqual(self.events,['move'])
        self.assertEqual(self.brain.snapshots[-1].context['inspection']['reason'],'InspectionTimeout')

    def test_shared_motion_resource_rejects_inspection(self):
        self.setup_session();self.output._motion_lock.acquire()
        try:self.turn()
        finally:self.output._motion_lock.release()
        self.assertEqual(self.events,[])
        self.assertEqual(self.brain.snapshots[-1].context['inspection']['reason'],'MotionResourceBusy')

    def test_extra_tool_after_inspection_is_rejected(self):
        self.setup_session()
        self.brain.reply=lambda s,c: iter([ToolCall('again','inspect_view',{'direction':'current','question':'Again?'})])
        self.assertEqual(self.turn().status,'failed')
        self.assertEqual(self.events,['move','settle','capture'])

    def test_continuation_payload_includes_image_without_enabling_more_tools(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Cancellation
        from tests.test_adapters import Client,Response
        self.setup_session();self.turn()
        brain=OpenAIBrain('synthetic')
        brain.client=Client(Response([{'type':'response.output_text.delta','delta':'A test image.'},{'type':'response.completed'}]))
        list(brain.reply(self.brain.snapshots[-1],Cancellation()))
        payload=brain.client.calls[0][1]['json']
        self.assertEqual(payload['input'][-1]['content'][-1]['type'],'input_image')
        self.assertNotIn('tools',payload)

    def test_cancelled_settle_trace_retains_completed_motion(self):
        self.setup_session();self.adapter.wait_until=lambda *args:self.session.cancel()
        self.turn()
        row=next(r for r in self.state.trace.rows if r['event']=='inspection_result')
        self.assertEqual(row['status'],'cancelled')
        self.assertEqual(row['motion_status'],'simulated')

    def test_changed_view_during_continuation_cancels_output(self):
        self.setup_session();original=self.brain.reply
        def reply(snapshot,cancel):
            if snapshot.continuation:self.state.attention.scene.invalidate(self.state.trace.clock())
            yield from original(snapshot,cancel)
        self.brain.reply=reply
        result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertFalse(result.completed_text)
