from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import json
import unittest

from r3x.contracts import Cancellation, Snapshot, ToolCall
from r3x.observations import Cue, PublicObservations, news_items, weather_item
from r3x.proactive import Opportunity
from r3x.settings import Settings
from r3x.scene import SceneObserver, encode_frame
from r3x.vision import Frame, FrameHub
from r3x.output import TextOutput, OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.runtime import VoiceRuntime
from r3x.adapters.hosted import OpenAIBrain
from tests.test_adapters import Client, Response
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import face
from tests.test_voice_core import Brain

NOW=datetime(2026,9,12,12,tzinfo=timezone.utc).timestamp()
URL='https://example.com/rss'
FEED=b'''<rss><channel><item><title>New &amp; improved telescope</title><link>https://example.com/story</link><pubDate>Sat, 12 Sep 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;A science headline.&lt;/b&gt;</description></item></channel></rss>'''


def weather(now=NOW):
    return json.dumps(dict(current=dict(time=now,temperature_2m=75,precipitation=0,weather_code=0,wind_speed_10m=5),
        current_units=dict(temperature_2m='°F',precipitation='inch',weather_code='wmo code',wind_speed_10m='mp/h'))).encode()


def frame(at=100,red=False):
    return Frame(uuid4().hex,'camera','session',at,48,27,(b'\x00\x00\xff' if red else b'\x40\x40\x40')*(48*27))


class SourceTests(unittest.TestCase):
    def test_feed_provenance_date_sanitation_dedup_and_stale_rejection(self):
        rows=news_items(FEED,URL,NOW)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].source_url,'https://example.com/story')
        self.assertNotIn('<b>',rows[0].text)
        self.assertIn('2026-09-12',rows[0].public()['published_utc'])
        self.assertEqual(news_items(FEED,URL,NOW+49*3600),())
        self.assertEqual(news_items(FEED.replace(b'2026 10:',b'2027 10:'),URL,NOW),())
        self.assertEqual(news_items(FEED.replace(b'https://example.com/story',b'file:///private/secret'),URL,NOW),())
        with self.assertRaises(ValueError):news_items(b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>',URL,NOW)

    def test_weather_freshness_units_and_semantic_condition_fingerprint(self):
        first=weather_item(weather(),'Sacramento',URL,NOW)
        second=weather_item(weather(NOW+300),'Sacramento',URL,NOW+300)
        self.assertEqual(first.cue_id,second.cue_id)
        self.assertIn('Sacramento',first.text)
        with self.assertRaises(ValueError):weather_item(weather(NOW-2000),'Sacramento',URL,NOW)
        with self.assertRaises(ValueError):weather_item(weather().replace(b'mp/h',b'km/h'),'Sacramento',URL,NOW)

    def test_refresh_failure_keeps_only_unexpired_evidence_and_offer_backoff(self):
        clock=[NOW];source=PublicObservations(Settings(news_feed_urls=(URL,),weather_enabled=False),clock=lambda:clock[0])
        source._get=lambda url:FEED;source.refresh();rows=source.candidates();self.assertEqual(len(rows),1)
        source.attempted([rows[0].cue_id]);self.assertFalse(source.candidates());self.assertTrue(source.valid(rows[0].cue_id))
        def fail(url):raise RuntimeError('synthetic network error')
        source._get=fail;clock[0]+=901;source.refresh();self.assertTrue(source.candidates())
        clock[0]+=6*3600;self.assertFalse(source.candidates());self.assertFalse(source.valid(rows[0].cue_id))
        source.close()

    def test_news_does_not_hide_weather_and_disabled_provider_makes_no_requests(self):
        source=PublicObservations(Settings(news_feed_urls=(),weather_enabled=False),clock=lambda:NOW)
        source._get=lambda _:self.fail('disabled fetch');source.refresh();self.assertFalse(source.candidates())
        source.rows={'news':tuple(Cue(str(i),'news','feed',URL,NOW,NOW+100,'headline') for i in range(16)),
                     'weather':(weather_item(weather(),'Sacramento',URL,NOW),)}
        self.assertIn('weather',[c.kind for c in source.candidates()])
        source.close()

    def test_settings_reject_invalid_coordinates_urls_and_undeclared_modes(self):
        for values in (dict(weather_latitude=float('nan')),dict(news_feed_urls=['http://example.com']),
                       dict(news_feed_urls=['https://user:pass@example.com']),dict(scene_observations_enabled=1)):
            with self.assertRaises(ValueError):Settings(**values)
        self.assertEqual(Settings().weather_location,'Sacramento, CA')

    def test_scene_is_bounded_paced_fresh_and_has_no_identity_side_channel(self):
        clock=[100];camera=SimpleNamespace(session_id='session',hub=FrameHub())
        scene=SceneObserver(camera,clock=lambda:clock[0]);scene.observe(frame())
        original,images=scene.snapshot();self.assertEqual(len(images),1)
        self.assertTrue(images[0].data_url.startswith('data:image/png;base64,'))
        scene.attempted(original);self.assertFalse(scene.snapshot())
        clock[0]=101;scene.observe(frame(101));self.assertEqual(scene.snapshot(True)[0],original)
        clock[0]=116;scene.observe(frame(116,True));self.assertNotEqual(scene.snapshot()[0],original)
        self.assertFalse(scene.valid(original));self.assertLessEqual(len(scene.images),2)
        clock[0]=119;self.assertFalse(scene.snapshot(True));scene.close()

    def test_scene_input_uses_same_response_call_without_changing_history(self):
        image,_=encode_frame(frame())
        response=Response([{'type':'response.output_text.delta','delta':'A red object.'},{'type':'response.completed'}])
        brain=OpenAIBrain('synthetic');brain.client=Client(response)
        snapshot=Snapshot('s','t',0,({'role':'user','content':'Earlier words'},),images=(image,))
        list(brain.reply(snapshot,Cancellation()))
        payload=brain.client.calls[0][1]['json']
        self.assertEqual(len(brain.client.calls),1)
        self.assertEqual(payload['input'][-1]['content'][-1]['type'],'input_image')
        self.assertEqual(len(snapshot.messages),1);self.assertNotIn('data:image',repr(snapshot))


class ObservationCoreTests(MemoryFixture):
    def setup_core(self, audio=False):
        self.state.wall_clock=lambda:NOW
        self.cue=Cue('news:test','news','Test source',URL,NOW,NOW+300,'A telescope is launching.')
        class Choice(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1;self.snapshots.append(snapshot)
                yield ToolCall(uuid4().hex,'use_observation',dict(cue_id=snapshot.context['observations'][0]['cue_id'],
                    text='A telescope launch made the headlines today.'))
        self.brain=Choice()
        self.session=Session('session',self.brain,OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace) if audio else TextOutput(self.state.trace),self.state.trace,self.state)

    def event(self):return Opportunity(uuid4().hex,'session',100,None,None,(face(100),),float('-inf'),False,'ambient',None,8,(self.cue,))

    def test_ambient_input_without_invented_human_turn_and_receipt_only_for_audio(self):
        self.setup_core()
        try:
            self.assertEqual(self.session.process_opportunity(self.event(),0,lambda:True).status,'completed')
            self.assertFalse(self.store.rows('SELECT * FROM turns'))
            self.assertFalse(self.store.rows('SELECT * FROM people'))
            self.assertFalse(self.store.rows('SELECT * FROM observation_delivery'))
            self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace)
            self.assertEqual(self.session.process_opportunity(self.event(),0,lambda:True).status,'completed')
            self.assertEqual(self.brain.calls,2);self.assertFalse(self.state.observation_available(self.cue.public()))
            self.assertEqual(len(self.store.rows('SELECT * FROM observation_delivery')),1)
        finally:self.session.close()

    def test_stale_or_unknown_cue_cannot_be_spoken(self):
        self.setup_core()
        try:
            context=self.state.opportunity_context(self.event());self.state.wall_clock=lambda:NOW+301
            result=self.state.execute_opportunity(ToolCall('x','use_observation',dict(cue_id=self.cue.cue_id,text='A headline.')),context,Cancellation())
            self.assertEqual(result.reason,'ObservationUnavailable')
        finally:self.session.close()

    def test_changed_provider_evidence_during_tts_cancels_before_playback(self):
        self.setup_core();valid=[True]
        self.state.observations=SimpleNamespace(valid=lambda _:valid[0])
        class Changed:
            def synthesize(self,text,cancel):valid[0]=False;yield bytes(64)
        self.session.output=OutputOwner(Changed(),SyntheticSink(),self.state.trace)
        try:
            result=self.session.process_opportunity(self.event(),0,lambda:True)
            self.assertEqual(result.status,'interrupted');self.assertEqual(result.completed_text,[])
            self.assertFalse(self.store.rows('SELECT * FROM observation_delivery'))
        finally:self.session.close()

    def test_production_scheduler_can_offer_new_material_without_new_speech(self):
        self.setup_core();source=PublicObservations(Settings(),clock=lambda:NOW);source.rows={'test':(self.cue,)}
        self.state.observations=source
        runtime=VoiceRuntime(SimpleNamespace(),SimpleNamespace(),None,self.session,self.state.trace,
            perception=SimpleNamespace(camera=SimpleNamespace()),observations=source)
        runtime.started_at=90;runtime.audio_seen_at=100
        runtime.camera.latest=lambda now:runtime.arrivals.faces
        for at in (97,98,99,100):runtime.arrivals.observe([face(at)],at)
        runtime.arrivals.seen['face']['used']=True
        try:
            runtime.proactive_tick();self.assertEqual(self.brain.calls,1)
            self.assertEqual(self.brain.snapshots[-1].context['trigger'],'ambient')
            runtime.proactive_tick();self.assertEqual(self.brain.calls,1)
        finally:self.session.close();source.close()

    def test_a_new_audience_or_a_human_floor_cancels_ambient_participation(self):
        self.setup_core()
        runtime=VoiceRuntime(SimpleNamespace(),SimpleNamespace(),None,self.session,self.state.trace,
            perception=SimpleNamespace(camera=SimpleNamespace()))
        try:
            event=self.event();runtime.arrivals.observe(event.faces,100)
            self.assertTrue(runtime.arrivals.valid(event,100))
            runtime.arrivals.observe((*event.faces,face(100,track='new')),100)
            self.assertFalse(runtime.arrivals.valid(event,100))
            self.state.initiative.feedback.append(dict(action='yield_floor',expires_at=220))
            result=self.session.process_opportunity(event,0,lambda:True)
            self.assertEqual(result.status,'suppressed');self.assertEqual(self.brain.calls,0)
        finally:self.session.close()

    def test_observation_selection_is_not_a_personal_write_tool(self):
        self.setup_core()
        try:
            context=self.state.opportunity_context(self.event())
            result=self.state.execute_opportunity(ToolCall('unsafe','record_claim',dict(subject_id='invented',
                subject_reference=None,predicate='mood',value='sad',kind='self_report',source_turn_id='fake')),context,Cancellation())
            self.assertEqual(result.outcome,'rejected');self.assertFalse(self.store.claims())
        finally:self.session.close()


class ObservationLifecycleTests(unittest.TestCase):
    def test_explicit_workers_cancel_close_and_release_buffers_without_devices(self):
        import threading
        source=PublicObservations(Settings(news_feed_urls=(),weather_enabled=False))
        source.refresh=lambda:None
        source.worker=threading.Thread(target=source._run)
        source.worker.start();source.close();self.assertFalse(source.worker.is_alive())
        camera=SimpleNamespace(session_id='session',hub=FrameHub())
        scene=SceneObserver(camera);scene.start();scene.close()
        self.assertFalse(scene.worker.is_alive());self.assertFalse(scene.images)

    def test_weather_change_requires_an_actual_prior_report(self):
        previous=weather_item(weather(),'Sacramento',URL,NOW)
        self.assertNotIn('previous_conditions',json.loads(previous.text))
        changed=weather_item(weather(NOW+300),'Sacramento',URL,NOW+300,previous)
        self.assertEqual(json.loads(changed.text)['previous_conditions']['temperature_2m'],75)

    def test_network_response_size_is_bounded_before_parsing(self):
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def raise_for_status(self):pass
            def iter_bytes(self):yield b'x'*1_000_001
        source=PublicObservations(Settings())
        source.client=SimpleNamespace(stream=lambda *a,**k:Stream(),close=lambda:None)
        from r3x.contracts import ProviderFailure
        with self.assertRaises(ProviderFailure):source._get(URL)
        source.close()
