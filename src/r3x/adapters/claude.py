"""Rex's replies from a Claude model (owner, November 10, after two blind comparisons on his own lines).

The request is the one OpenAIBrain builds (persona, history, the notes for this moment, the tools); only its
wire form and the stream that comes back differ. The planner, joke writer, locator and news search stay
where they were. docs/CLAUDE_REPLIES.md has the evidence and what is still unchecked.
"""
import json
import time

from ..contracts import Cancelled, ProviderFailure, ToolCall, ToolText
from ..providers import Capability
from .hosted import OpenAIBrain

URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2026-10-21'
MODELS = ('claude-sonnet-5-5', 'claude-haiku-5-5')
# Sonnet 5.5 cannot switch thinking off; at low effort it thinks briefly or not at all. Haiku 5.5 can.
THINKING = {'claude-sonnet-5-5': {'type': 'adaptive'}, 'claude-haiku-5-5': {'type': 'disabled'}}
MAX_TOKENS = 4000
OPERATOR = '[Operator note, not said by anyone in the room]\n'


def reply_brain(settings, secrets):
    """The reply model the settings choose; the one place live and replay runs get it from."""
    if settings.reply_provider == 'claude':
        return ClaudeBrain(secrets.anthropic_api_key, settings.claude_reply_model)
    return OpenAIBrain(secrets.openai_api_key, settings.reply_service_tier)


def _blocks(content):
    """OpenAI message content as Claude content blocks."""
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content.strip() else []
    blocks = []
    for part in content:
        if part.get('type') == 'input_image':
            media, _, data = part['image_url'].partition(';base64,')
            blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': media.removeprefix('data:'),
                                                       'data': data}})
        elif part.get('text'):
            blocks.append({'type': 'text', 'text': part['text']})
    return blocks


def translate(payload, offered):
    """(system, messages, tools, tool_choice) for the Messages API from an OpenAI Responses payload.

    Tools and persona are the same bytes on every request and are stored for an hour; the history up to the
    newest heard line is stored for five minutes. Notes for this moment follow and are never stored.
    """
    system = [{'type': 'text', 'text': payload['instructions'], 'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}]
    messages, items, stable = [], list(payload['input']), None

    def add(role, blocks, history=False):
        nonlocal stable
        if messages and messages[-1]['role'] == role and role != 'system':
            messages[-1]['content'].extend(blocks)   # One turn per speaker: tool results stay together.
        else:
            messages.append({'role': role, 'content': blocks})
        if history:
            stable = (len(messages) - 1, len(messages[-1]['content']) - 1)

    index = 0
    while index < len(items):
        item = items[index]
        role, kind = item.get('role'), item.get('type')
        if role == 'developer':
            text = item['content']
            while index + 1 < len(items) and items[index + 1].get('role') == 'developer':
                index += 1
                text += '\n\n' + items[index]['content']
            following = items[index + 1] if index + 1 < len(items) else None
            if not messages:
                system.append({'type': 'text', 'text': text})   # Constants for the run, ahead of the history.
            elif (messages[-1]['role'] == 'user' and not any(b['type'] == 'tool_result' for b in messages[-1]['content'])
                  and (following is None or following.get('role') == 'assistant')):
                messages.append({'role': 'system', 'content': text})
            else:
                add('user', [{'type': 'text', 'text': OPERATOR + text}])
        elif role in ('user', 'assistant'):
            if not messages and role == 'assistant':
                add('user', [{'type': 'text', 'text': '(Rex spoke first.)'}])
            blocks = _blocks(item['content'])
            if blocks:
                add(role, blocks, history=not any(b['type'] == 'image' for b in blocks))
        elif kind == 'function_call':
            if not messages:
                add('user', [{'type': 'text', 'text': '(Rex acted first.)'}])
            add('assistant', [{'type': 'tool_use', 'id': item['call_id'], 'name': item['name'],
                               'input': json.loads(item.get('arguments') or '{}')}], history=True)
        elif kind == 'function_call_output':
            output = item.get('output')
            add('user', [{'type': 'tool_result', 'tool_use_id': item['call_id'],
                          'content': output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)}],
                history=True)
        index += 1
    if stable is not None:
        messages[stable[0]]['content'][stable[1]]['cache_control'] = {'type': 'ephemeral'}
    tools = [{'name': t['name'], 'description': t['description'], 'input_schema': t['parameters'],
              'eager_input_streaming': True} for t in payload.get('tools', ())]
    choice = payload.get('tool_choice')
    required = False
    if not tools:
        choice = None
    elif choice == 'none' or not offered:
        choice = {'type': 'none'}
    else:
        # Claude has no way to offer a subset of the stored tools, and Sonnet 5.5 cannot be forced to call
        # one. The moment's tools are named last; the answer is checked against them when it comes back.
        required = isinstance(choice, dict) and choice.get('mode') == 'required'
        note = ('TOOLS FOR THIS TURN: ' + ', '.join(d['name'] for d in offered) + '. No other tool is available now.'
                + (' Answer with exactly one of these tool calls, not plain text.' if required else ''))
        if messages and messages[-1]['role'] == 'system':
            messages[-1]['content'] += '\n\n' + note
        elif messages and messages[-1]['role'] == 'user' and not any(b['type'] == 'tool_result' for b in messages[-1]['content']):
            messages.append({'role': 'system', 'content': note})
        else:
            add('user', [{'type': 'text', 'text': OPERATOR + note}])
        choice = {'type': 'auto', 'disable_parallel_tool_use': True}
    return system, messages, tools, choice, required


class ClaudeBrain(OpenAIBrain):
    RETRYABLE = frozenset({
        'ClaudeConnectError', 'ClaudeConnectTimeout', 'ClaudeReadError', 'ClaudeReadTimeout', 'ClaudeWriteError',
        'ClaudeWriteTimeout', 'ClaudePoolTimeout', 'ClaudeRemoteProtocolError', 'ClaudeHTTP408', 'ClaudeHTTP409',
        'ClaudeHTTP429', 'ClaudeHTTP500', 'ClaudeHTTP502', 'ClaudeHTTP503', 'ClaudeHTTP504', 'ClaudeHTTP529',
        'ClaudeOverloaded'})

    def __init__(self, key: str, model: str = MODELS[0]):
        super().__init__(key)
        if model not in MODELS:
            raise ValueError('UnsupportedClaudeReplyModel')
        self.capability = Capability('brain', 'Anthropic Messages', 'one main conversational generation',
                                     model, ('httpx',), ('network',))

    def _stream_response(self, payload, snapshot, cancel, offered=None):
        model = self.capability.model
        system, messages, tools, choice, required = translate(payload, offered)
        request = {'model': model, 'max_tokens': MAX_TOKENS, 'system': system, 'messages': messages, 'stream': True,
                   'thinking': THINKING[model], 'output_config': {'effort': 'low'}}
        if tools:
            request.update(tools=tools, tool_choice=choice)
        allowed = {d['name'] for d in offered} if offered else set()
        try:
            for attempt in range(2):
                out = {}
                yield from self._round(request, snapshot, cancel, allowed, out)
                calls, texts, stop = out['calls'], out['texts'], out['stop']
                strays = [c for c in calls.values() if c['name'] not in allowed]
                if strays and attempt == 0 and stop == 'tool_use' and not out['released']:
                    # Claude sees every stored tool and now and then reaches for one the moment does not
                    # offer. Nothing has been said or done yet: it is told so and answers again, once.
                    if self.trace:
                        self.trace.emit('tool_unoffered', turn_id=snapshot.turn_id, names=[c['name'] for c in strays])
                    request['messages'] = request['messages'] + [
                        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': c['id'], 'name': c['name'],
                                                           'input': self._parsed(c['json'])} for c in calls.values()]},
                        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': c['id'], 'is_error': True,
                                                      'content': 'Nothing was done. That tool is not available this turn. '
                                                                 'Available now: ' + ', '.join(sorted(allowed)) + '.'}
                                                     for c in calls.values()]}]
                    continue
                break
            if stop in ('refusal', 'max_tokens'):
                if self.trace:
                    self.trace.emit('model_stream_error', turn_id=snapshot.turn_id, kind='stopped', code=stop)
                raise ProviderFailure('ClaudeRefusal' if stop == 'refusal' else 'ModelIncomplete')
            if calls:
                from ..semantics import ground
                if self.trace and ''.join(texts).strip():
                    self.trace.emit('reply_text_beside_tool', turn_id=snapshot.turn_id, characters=len(''.join(texts)),
                                    tools=[c['name'] for c in calls.values()])
                for call in calls.values():
                    if call['name'] not in allowed:
                        raise ProviderFailure('UnofferedTool')
                    try:
                        arguments = ground(call['name'], json.loads(call['json'] or '{}'), offered)
                    except ValueError as exc:   # Unfinished JSON, or a value the moment does not offer.
                        raise ProviderFailure(str(exc) if not isinstance(exc, json.JSONDecodeError) else 'ModelIncomplete') from None
                    yield ToolCall(call['id'], call['name'], arguments)
            elif required:
                raise ProviderFailure('ToolRequired')
            else:
                yield from texts
        except (Cancelled, ProviderFailure):
            raise
        except Exception as exc:
            raise ProviderFailure('Claude' + type(exc).__name__) from None

    def _round(self, request, snapshot, cancel, allowed, out):
        """One request and its stream. Spoken words are yielded as they arrive; the rest is left in `out`."""
        from ..tool_text import STREAMED_FIELDS, TextField
        model = request['model']
        deadline = time.monotonic() + 200
        usage, stop, complete = {}, None, False
        texts, calls, spoken = [], {}, {}
        out.update(calls=calls, texts=texts, stop=None, released=False)
        with self.client.stream('POST', URL, json=request, headers={
                'x-api-key': self.key, 'anthropic-version': API_VERSION, 'content-type': 'application/json'}) as response:
            if response.status_code != 200:
                raise ProviderFailure(self._refused(response))
            for line in response.iter_lines():
                cancel.check()
                if time.monotonic() > deadline:
                    raise ProviderFailure('ModelDeadline')
                if not line.startswith('data:'):
                    continue
                event = json.loads(line[5:].strip())
                kind = event.get('type')
                if kind == 'message_start':
                    usage.update(event.get('message', {}).get('usage') or {})
                elif kind == 'content_block_start' and event['content_block'].get('type') == 'tool_use':
                    block = event['content_block']
                    if len(calls) >= 4:
                        raise ProviderFailure('ToolCallLimit')
                    calls[event['index']] = {'id': block['id'], 'name': block['name'], 'json': ''}
                    if snapshot.stream_tool_text and block['name'] in STREAMED_FIELDS and block['name'] in allowed:
                        spoken[event['index']] = TextField(STREAMED_FIELDS[block['name']])
                elif kind == 'content_block_delta':
                    delta = event['delta']
                    if delta.get('type') == 'text_delta':
                        if snapshot.tools_enabled:
                            texts.append(delta['text'])   # Words beside a tool call are never said.
                            if sum(map(len, texts)) > 4000:
                                raise ProviderFailure('UnphasedReplyLengthLimit')
                        else:
                            paused = time.monotonic()
                            out['released'] = True
                            yield delta['text']
                            deadline += time.monotonic() - paused
                    elif delta.get('type') == 'input_json_delta' and event['index'] in calls:
                        call = calls[event['index']]
                        call['json'] += delta.get('partial_json', '')
                        if len(call['json']) > 8000:
                            raise ProviderFailure('ToolCallLimit')
                        field = spoken.get(event['index'])
                        if field is not None:
                            words = field.feed(delta.get('partial_json', ''))
                            finished = field.state == 'done'
                            if words or finished:
                                paused = time.monotonic()
                                out['released'] = True
                                yield ToolText(call['id'], call['name'], words, finished)
                                deadline += time.monotonic() - paused
                            if finished:
                                del spoken[event['index']]
                elif kind == 'message_delta':
                    stop = event.get('delta', {}).get('stop_reason') or stop
                    usage.update(event.get('usage') or {})
                elif kind == 'message_stop':
                    complete = True
                elif kind == 'error':
                    code = str((event.get('error') or {}).get('type'))[:80]
                    if self.trace:
                        self.trace.emit('model_stream_error', turn_id=snapshot.turn_id, kind=kind, code=code)
                    raise ProviderFailure('ClaudeOverloaded' if code == 'overloaded_error' else
                                          'ClaudeCreditExhausted' if code == 'billing_error' else 'ModelIncomplete')
        if not complete:
            raise ProviderFailure('ModelStreamTruncated')
        read, write = usage.get('cache_read_input_tokens') or 0, usage.get('cache_creation_input_tokens') or 0
        if self.trace:
            self.trace.emit('model_usage', model=model, turn_id=snapshot.turn_id,
                            input_tokens=(usage.get('input_tokens') or 0) + read + write, cached_tokens=read,
                            cache_write_tokens=write, output_tokens=usage.get('output_tokens') or 0)
        out['stop'] = stop

    @staticmethod
    def _parsed(text):
        try:
            value = json.loads(text or '{}')
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _refused(response) -> str:
        """The failure a refused request stands for. Out of credit is not retried; Rex says a saved line."""
        try:
            error = json.loads(response.read() or b'{}').get('error') or {}
        except Exception:
            error = {}
        if (response.status_code == 402 or error.get('type') == 'billing_error'
                or 'credit balance' in str(error.get('message', '')).lower()):
            return 'ClaudeCreditExhausted'
        return f'ClaudeHTTP{response.status_code}'
