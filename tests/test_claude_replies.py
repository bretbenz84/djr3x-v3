"""Rex's replies from a Claude model (owner, November 10). Fake HTTP responses in the shape Anthropic's
streaming documentation shows; no network. What a live Claude model says is not proven here."""
import json
import unittest

from r3x.adapters.claude import ClaudeBrain, reply_brain, translate
from r3x.adapters.hosted import OpenAIBrain, PERSONA
from r3x.contracts import Cancellation, ProviderFailure, Snapshot, ToolCall, ToolText
from r3x.session import CREDIT_LINES
from r3x.settings import Secrets, Settings
from tests.test_adapters import Client, Response
from tests.test_credit_notice import Refused

HEARD = ({'role': 'user', 'content': 'Hello, Rex.'}, {'role': 'assistant', 'content': 'There you are.'},
         {'role': 'user', 'content': 'I took your arm off.'})


def stream(*blocks, stop='end_turn', usage=None):
    """Anthropic stream events for content blocks: ('text', words) or ('tool', id, name, [json pieces])."""
    events = [{'type': 'message_start', 'message': {'usage': usage or {
        'input_tokens': 10, 'cache_read_input_tokens': 70, 'cache_creation_input_tokens': 20}}}]
    for index, block in enumerate(blocks):
        if block[0] == 'text':
            events += [{'type': 'content_block_start', 'index': index, 'content_block': {'type': 'text', 'text': ''}},
                       {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': block[1]}}]
        else:
            events.append({'type': 'content_block_start', 'index': index,
                           'content_block': {'type': 'tool_use', 'id': block[1], 'name': block[2], 'input': {}}})
            events += [{'type': 'content_block_delta', 'index': index,
                        'delta': {'type': 'input_json_delta', 'partial_json': piece}} for piece in block[3]]
        events.append({'type': 'content_block_stop', 'index': index})
    return Response(events + [{'type': 'message_delta', 'delta': {'stop_reason': stop}, 'usage': {'output_tokens': 7}},
                              {'type': 'message_stop'}])


class Rows:
    def __init__(self): self.rows = []
    def emit(self, event, **data): self.rows.append({'event': event, **data})


def brain(response, model='claude-sonnet-5-5'):
    made = ClaudeBrain('fixture', model)
    made.client, made.trace = Client(response), Rows()
    made._wait_retry = lambda seconds, cancel: None
    return made


def sent(made):
    return made.client.calls[-1][1]['json']


class RequestTests(unittest.TestCase):
    def test_persona_and_tools_are_stored_for_an_hour_and_the_moments_notes_come_last(self):
        made = brain(stream(('text', 'Give it back.')))
        said = list(made.reply(Snapshot('s', 't', 0, HEARD, context={'right_now': {'place': 'workshop'}, 'source_turn_id': 't'},
                                        tools_enabled=True), Cancellation()))
        self.assertEqual(said, ['Give it back.'])
        args, kwargs = made.client.calls[-1]
        self.assertEqual(args, ('POST', 'https://api.anthropic.com/v1/messages'))
        self.assertEqual(kwargs['headers']['x-api-key'], 'fixture')
        request = kwargs['json']
        self.assertEqual(request['model'], 'claude-sonnet-5-5')
        self.assertEqual(request['system'][0], {'type': 'text', 'text': PERSONA,
                                                'cache_control': {'type': 'ephemeral', 'ttl': '1h'}})
        from r3x.semantics import TOOLS
        self.assertEqual([t['name'] for t in request['tools']], [t['name'] for t in TOOLS])   # The same on every request.
        self.assertTrue(all(t['input_schema']['type'] == 'object' and t['eager_input_streaming'] for t in request['tools']))
        self.assertEqual(request['tool_choice'], {'type': 'auto', 'disable_parallel_tool_use': True})
        roles = [m['role'] for m in request['messages']]
        self.assertEqual(roles, ['user', 'assistant', 'user', 'system'])
        newest = request['messages'][2]['content'][-1]
        self.assertEqual((newest['text'], newest['cache_control']), ('I took your arm off.', {'type': 'ephemeral'}))
        notes = request['messages'][3]['content']
        self.assertIn('CURRENT CORE CONTEXT', notes)
        self.assertIn("Rex's voice for this reply", notes)
        self.assertIn('TOOLS FOR THIS TURN: ', notes)
        self.assertNotIn('cache_control', json.dumps(request['messages'][3]))

    def test_haiku_runs_without_thinking_and_an_unknown_model_is_refused(self):
        made = brain(stream(('text', 'Fine.')), 'claude-haiku-5-5')
        list(made.reply(Snapshot('s', 't', 0, HEARD), Cancellation()))
        self.assertEqual(sent(made)['thinking'], {'type': 'disabled'})
        self.assertEqual(sent(made)['output_config'], {'effort': 'low'})
        with self.assertRaisesRegex(ValueError, 'UnsupportedClaudeReplyModel'):
            ClaudeBrain('fixture', 'claude-opus-5-5')

    def test_earlier_tool_calls_views_and_run_constants_keep_their_place(self):
        payload = {'instructions': 'persona', 'input': [
            {'role': 'developer', 'content': 'STATIC CONTEXT'},
            {'role': 'user', 'content': 'What do you see?'},
            {'type': 'function_call', 'call_id': 'c1', 'name': 'inspect_view', 'arguments': '{"direction": "current"}'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': '{"ok": true}'},
            {'role': 'developer', 'content': 'Describe only what the view shows.'},
            {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Attached visual evidence.'},
                                         {'type': 'input_image', 'image_url': 'data:image/jpeg;base64,QUJD', 'detail': 'low'}]}],
            'tools': [{'type': 'function', 'name': 'inspect_view', 'description': 'Look.', 'strict': True,
                       'parameters': {'type': 'object', 'properties': {}}}], 'tool_choice': 'none'}
        system, messages, tools, choice, required = translate(payload, None)
        self.assertEqual([b['text'] for b in system], ['persona', 'STATIC CONTEXT'])
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user'])
        self.assertEqual(messages[1]['content'], [{'type': 'tool_use', 'id': 'c1', 'name': 'inspect_view',
                                                   'input': {'direction': 'current'}}])
        result, note, words, image = messages[2]['content']
        self.assertEqual((result['type'], result['tool_use_id'], result['cache_control']),
                         ('tool_result', 'c1', {'type': 'ephemeral'}))
        self.assertTrue(note['text'].startswith('[Operator note'))
        self.assertEqual(image['source'], {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'QUJD'})
        self.assertEqual((choice, required), ({'type': 'none'}, False))
        self.assertEqual(tools[0]['input_schema'], {'type': 'object', 'properties': {}})


class AnswerTests(unittest.TestCase):
    def ask(self, response, **context):
        made = brain(response)
        snapshot = Snapshot('s', 't', 0, HEARD, context={'source_turn_id': 't', **context}, tools_enabled=True,
                            stream_tool_text=True)
        return made, made.reply(snapshot, Cancellation())

    def test_a_reply_tools_words_are_spoken_as_they_arrive_and_usage_is_recorded(self):
        made, reply = self.ask(stream(('text', 'Let me answer.'), ('tool', 'toolu_1', 'remain_silent',
                                      ['{"reason": "no_resp', 'onse_needed", "invitation_update": null, ',
                                       '"intent_update": null, "next_opportunity": null}'])))
        items = list(reply)
        self.assertEqual([type(i) for i in items], [ToolCall])   # Words beside a tool call are never said.
        self.assertEqual((items[0].call_id, items[0].name, items[0].arguments['reason']),
                         ('toolu_1', 'remain_silent', 'no_response_needed'))
        usage = [r for r in made.trace.rows if r['event'] == 'model_usage'][0]
        self.assertEqual({k: usage[k] for k in ('model', 'input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens')},
                         {'model': 'claude-sonnet-5-5', 'input_tokens': 100, 'cached_tokens': 70,
                          'cache_write_tokens': 20, 'output_tokens': 7})
        made, reply = self.ask(stream(('tool', 'toolu_2', 'personal_history',
                                       ['{"say_first": "Ooh, ', 'hang on.", "section": "star_tours"}'])))
        items = list(reply)
        heard = ''.join(i.text for i in items if isinstance(i, ToolText))
        self.assertEqual(heard, 'Ooh, hang on.')
        self.assertIsInstance(items[-1], ToolCall)

    def test_a_tool_the_moment_does_not_offer_a_refusal_and_a_cut_off_answer_fail_as_structured_results(self):
        for response, failure in (
                (stream(('text', 'No.'), stop='refusal'), 'ClaudeRefusal'),
                (stream(('tool', 'toolu_1', 'remain_silent', ['{"reason": "no_']), stop='max_tokens'), 'ModelIncomplete'),
                (Response([{'type': 'message_start', 'message': {'usage': {}}}]), 'ModelStreamTruncated')):
            made, reply = self.ask(response)
            with self.assertRaisesRegex(ProviderFailure, failure):
                list(reply)
            self.assertEqual(len(made.client.calls), 1)

    def test_a_tool_the_moment_does_not_offer_is_refused_and_asked_again_once(self):
        class Two(Client):
            def stream(self, *args, **kwargs):
                self.calls.append((args, json.loads(json.dumps(kwargs))))
                return self.response.pop(0)
        stray = lambda: stream(('tool', 'toolu_1', 'drive_base', ['{"steps": []}']), stop='tool_use')
        made = brain(None)
        made.client = Two([stray(), stream(('text', 'Staying put.'))])
        said = list(made.reply(Snapshot('s', 't', 0, HEARD, context={'source_turn_id': 't'}, tools_enabled=True), Cancellation()))
        self.assertEqual(said, ['Staying put.'])
        told = made.client.calls[1][1]['json']['messages'][-2:]
        self.assertEqual(told[0]['content'][0]['name'], 'drive_base')
        self.assertTrue(told[1]['content'][0]['is_error'])
        self.assertIn('remain_silent', told[1]['content'][0]['content'])
        self.assertEqual([r['names'] for r in made.trace.rows if r['event'] == 'tool_unoffered'], [['drive_base']])
        made.client = Two([stray(), stray()])   # A second stray is a failed turn, never an action.
        with self.assertRaisesRegex(ProviderFailure, 'UnofferedTool'):
            list(made.reply(Snapshot('s', 't', 0, HEARD, context={'source_turn_id': 't'}, tools_enabled=True), Cancellation()))

    def test_a_turn_that_must_use_a_tool_never_becomes_spoken_words(self):
        payload = {'instructions': 'p', 'input': [{'role': 'user', 'content': 'Hi.'}],
                   'tools': [{'type': 'function', 'name': 'remain_silent', 'description': 'd', 'parameters': {'type': 'object'}}],
                   'tool_choice': {'type': 'allowed_tools', 'mode': 'required', 'tools': [{'type': 'function', 'name': 'remain_silent'}]}}
        made = brain(stream(('text', 'Hello anyway.')))
        with self.assertRaisesRegex(ProviderFailure, 'ToolRequired'):
            list(made._stream_response(payload, Snapshot('s', 't', 0, (), tools_enabled=True), Cancellation(),
                                       [{'name': 'remain_silent'}]))
        self.assertIn('exactly one of these tool calls', sent(made)['messages'][-1]['content'])

    def test_out_of_credit_is_said_not_retried_and_an_overload_waits_and_tries_again(self):
        for refused in (Refused(402, b'{"type": "error", "error": {"type": "billing_error", "message": "x"}}'),
                        Refused(400, b'{"type": "error", "error": {"type": "invalid_request_error", "message": '
                                     b'"Your credit balance is too low to access the Anthropic API."}}')):
            made, reply = self.ask(refused)
            with self.assertRaisesRegex(ProviderFailure, 'ClaudeCreditExhausted'):
                list(reply)
            self.assertEqual(len(made.client.calls), 1)
        self.assertIn('ClaudeCreditExhausted', CREDIT_LINES)
        made, reply = self.ask(Refused(401, b'{"error": {"type": "authentication_error"}}'))
        with self.assertRaisesRegex(ProviderFailure, 'ClaudeHTTP401'):
            list(reply)
        self.assertEqual(len(made.client.calls), 1)
        self.assertLessEqual({'ClaudeHTTP429', 'ClaudeHTTP529', 'ClaudeOverloaded', 'ClaudeReadTimeout'}, ClaudeBrain.RETRYABLE)
        self.assertFalse({'ClaudeCreditExhausted', 'ClaudeRefusal', 'ClaudeHTTP401'} & ClaudeBrain.RETRYABLE)


class ChoiceTests(unittest.TestCase):
    def test_settings_choose_the_reply_model_and_openai_stays_the_default(self):
        secrets = Secrets(openai_api_key='o', elevenlabs_api_key='e', anthropic_api_key='a')
        chosen = reply_brain(Settings(), secrets)
        self.assertEqual((type(chosen), chosen.capability.model, chosen.key), (OpenAIBrain, 'gpt-6-luna', 'o'))
        chosen = reply_brain(Settings(llm_provider='claude', claude_reply_model='claude-haiku-5-5'), secrets)
        self.assertEqual((type(chosen), chosen.capability.model, chosen.key), (ClaudeBrain, 'claude-haiku-5-5', 'a'))
        for wrong in ({'llm_provider': 'other'}, {'claude_reply_model': 'claude-opus-5-5'}):
            with self.assertRaises(ValueError):
                Settings(**wrong)
        self.assertNotIn('anthropic_api_key', Secrets(openai_api_key='o', elevenlabs_api_key='e').readiness())

    def test_the_famous_voices_open_every_request_once_seen(self):
        """November 10: present on a person's turn and missing from Rex's own, the block changed the start
        of the request and the stored prompt stopped matching."""
        made = OpenAIBrain('fixture')
        done = {'type': 'response.completed', 'response': {}}
        famous = [{'voice': 'reagan', 'name': 'Ronald Reagan'}]
        starts = []
        for context in ({'impersonation': {'available': True, 'famous': famous}}, {'trigger': 'conversation_lull'},
                        {'impersonation': {'available': True, 'famous': ['reagan']}}):
            made.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Hm.'}, done]))
            list(made.reply(Snapshot('s', 't', 0, HEARD, context=context), Cancellation()))
            starts.append(made.client.calls[-1][1]['json']['input'][0])
        self.assertEqual(starts[0]['role'], 'developer')
        self.assertIn('Ronald Reagan', starts[0]['content'])
        self.assertEqual(starts[0], starts[1])
        self.assertEqual(starts[0], starts[2])


class Whole:
    """An unstreamed answer."""
    status_code = 200
    def __init__(self, body): self.body = body
    def json(self): return self.body


class Posts:
    def __init__(self, *bodies): self.bodies, self.calls = list(bodies), []
    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return Whole(self.bodies.pop(0))


class Image:
    frame_id, data_url = 'f1', 'data:image/jpeg;base64,AAAA'


class HelperTests(unittest.TestCase):
    """The goal planner, picture locator and news search on Claude (owner, November 10)."""

    def test_the_settings_choose_one_provider_for_every_hosted_model(self):
        from r3x.adapters import claude
        from r3x.adapters.hosted import OpenAIAgencyPlanner, OpenAINewsSearch, OpenAIVisualLocator
        secrets = Secrets(openai_api_key='o', anthropic_api_key='a')
        on_claude, on_openai = Settings(llm_provider='claude'), Settings()
        made = [claude.agency_planner(on_claude, secrets), claude.visual_locator(on_claude, secrets),
                claude.news_search(on_claude, secrets)]
        self.assertEqual([m.capability.model for m in made], ['claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-sonnet-5-5'])
        self.assertEqual({m.key for m in made}, {'a'})
        self.assertEqual({m.capability.provider for m in made} & {'OpenAI Responses'}, set())
        back = [claude.agency_planner(on_openai, secrets), claude.visual_locator(on_openai, secrets),
                claude.news_search(on_openai, secrets)]
        self.assertEqual([type(m) for m in back], [OpenAIAgencyPlanner, OpenAIVisualLocator, OpenAINewsSearch])
        self.assertEqual({m.key for m in back}, {'o'})

    def test_the_planner_asks_for_one_plan_in_a_fixed_shape_and_reads_it_back(self):
        from r3x.adapters.claude import ClaudeAgencyPlanner
        from r3x.adapters.hosted import CHARACTER
        plan = {'goal_kind': 'x', 'steps': []}
        planner = ClaudeAgencyPlanner('fixture')
        planner.client, planner.trace = Client(stream(('text', json.dumps(plan)))), Rows()
        self.assertEqual(planner.plan({'request_id': 'p', 'revision': 0, 'evidence': {}}, Cancellation(), (Image(),)), plan)
        request = sent(planner)
        self.assertEqual(request['model'], 'claude-sonnet-5-5')
        self.assertTrue(request['system'][0]['text'].startswith(CHARACTER))
        self.assertNotIn('tools', request)
        shape = json.dumps(request['output_config']['format']['schema'])
        for refused in ('maxLength', 'minLength', 'maxItems', 'minItems', 'minimum', 'maximum'):
            self.assertNotIn(refused, shape)
        self.assertIn('at most 240 characters', shape)
        self.assertEqual([b['type'] for b in request['messages'][0]['content']], ['text', 'text', 'image'])
        self.assertEqual(planner.trace.rows[-1]['event'], 'model_usage')

    def test_a_plan_that_is_cut_short_or_not_json_is_a_missing_plan(self):
        from r3x.adapters.claude import ClaudeAgencyPlanner
        for response in (stream(('text', '{"goal_kind": "x"'), stop='max_tokens'), stream(('text', 'Here is my plan.'))):
            planner = ClaudeAgencyPlanner('fixture')
            planner.client, planner.trace = Client(response), Rows()
            with self.assertRaises(ProviderFailure) as failed:
                planner.plan({'request_id': 'p', 'revision': 0}, Cancellation())
            self.assertEqual(str(failed.exception), 'AgencyPlanMissing')

    def test_a_look_goes_to_the_quick_model_with_the_picture_and_counts_against_the_budget(self):
        from r3x.adapters.claude import ClaudeVisualLocator
        found = {'found': True, 'x': .4, 'y': .5, 'width': .1, 'height': .2, 'confidence': 'high', 'note': 'mug'}
        locator = ClaudeVisualLocator('fixture')
        locator.client = Posts({'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(found)}],
                                'usage': {'input_tokens': 9}})
        self.assertEqual(locator.locate('the red mug', Image(), Cancellation()), found)
        args, kwargs = locator.client.calls[0]
        request = kwargs['json']
        self.assertEqual((request['model'], request['thinking']), ('claude-haiku-5-5', {'type': 'disabled'}))
        self.assertEqual(kwargs['headers']['x-api-key'], 'fixture')
        self.assertEqual(request['messages'][0]['content'][1]['source'],
                         {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'AAAA'})
        self.assertNotIn('minimum', json.dumps(request['output_config']))
        self.assertEqual(locator.calls, 1)
        locator.client = Posts({'stop_reason': 'refusal', 'content': []})
        with self.assertRaises(ProviderFailure):
            locator.route(Image(), {'goal': 'kitchen'}, Cancellation())

    def test_news_resumes_a_paused_search_and_returns_the_pages_it_was_shown(self):
        from r3x.adapters.claude import ClaudeNewsSearch
        results = {'type': 'web_search_tool_result', 'tool_use_id': 's1', 'content': [
            {'type': 'web_search_result', 'url': 'https://example.org/a', 'title': 'A'}]}
        paused = {'stop_reason': 'pause_turn', 'usage': {'input_tokens': 5, 'server_tool_use': {'web_search_requests': 1}},
                  'content': [{'type': 'server_tool_use', 'id': 's1', 'name': 'web_search', 'input': {}}, results]}
        stories = {'stories': [{'headline': 'A'}]}
        done = {'stop_reason': 'end_turn', 'usage': {'input_tokens': 7, 'server_tool_use': {'web_search_requests': 1}},
                'content': [{'type': 'text', 'text': 'Searching.'}, results, {'type': 'text', 'text': json.dumps(stories)}]}
        search = ClaudeNewsSearch('fixture')
        search.client = Posts(paused, done)
        answer, sources, usage = search.search('Find today.')
        self.assertEqual(answer, stories)
        self.assertEqual(sources, ['https://example.org/a', 'https://example.org/a'])
        self.assertEqual((usage['web_search_calls'], usage['input_tokens']), (2, 12))
        resumed = search.client.calls[1][1]['json']['messages']
        self.assertEqual([m['role'] for m in resumed], ['user', 'assistant'])
        self.assertEqual(resumed[1]['content'], paused['content'])

    def test_out_of_credit_on_a_helper_is_named_as_such(self):
        from r3x.adapters.claude import ClaudeNewsSearch
        class Broke:
            def post(self, *args, **kwargs): return Refused(400, b'{"error": {"type": "billing_error", "message": "x"}}')
        search = ClaudeNewsSearch('fixture'); search.client = Broke()
        with self.assertRaises(ProviderFailure) as failed:
            search.search('Find today.')
        self.assertEqual(str(failed.exception), 'ClaudeCreditExhausted')


if __name__ == '__main__':
    unittest.main()
