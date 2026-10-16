"""Explicit paid latency probe for replies: when could Rex start speaking? Synthetic content only.

Run: .venv/bin/python tools/check_reply_latency.py --live [--rounds 6] [--configs gpt-6-luna,gpt-6-luna@fast]

Sends a production-sized request (persona, synthetic history and context, the static tool
list) to the reply model, paced, and times from the request: the first streamed output, the
first speakable phrase if a reply tool's words are spoken as they stream (October 14 plan,
step 5), and the finished response (when a tool reply could start speaking before). Arms:
`static` sends the byte-identical static tool list with allowed_tools (production since
September 18, prompt-cache friendly); `subset` sends only the allowed tools; `tool` is static
but requires a reply tool (respond_with_gesture or respond_with_intent), the route 170 of 205
live replies took. `--configs` compares reply models and service tiers (`model@tier`, e.g. `@fast`;
no tier is Standard); the response's served tier is recorded because Fast traffic can be downgraded.
The question invites a two-sentence answer. Private results go to local/validation/reply-latency-*/.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from r3x.adapters.hosted import PERSONA
from r3x.output import Phrases
from r3x.semantics import TOOLS
from r3x.settings import Secrets
from r3x.tool_text import STREAMED_TOOLS, TextField

MODEL, PACE = 'gpt-6-luna', 4.
NO_REASONING = ('gpt-4',)   # Non-reasoning families reject the reasoning parameter.
HUMAN_TOOLS = sorted(d['name'] for d in TOOLS if d['name'] not in {'wait_for', 'revise_agency_plan', 'describe_view'})
QUESTION = ("I just repainted the kitchen in eight different shades of beige and then spilled one on the dog. "
            "Be honest, Rex: how bad is it?")


def history():
    rows = []
    for i, topic in enumerate(('the weather', 'disco records', 'a broken toaster', 'a road trip', 'gardening', 'coffee')):
        rows.append({'role': 'developer', 'content': 'Evidence for the immediately following utterance: ' + json.dumps(
            {'source_turn_id': f'turn{i}', 'speaker': {'state': 'supported', 'person_id': 'p1', 'display_name': 'Sam'},
             'faces': [{'track_id': 'face-1', 'identity': 'p1'}]})})
        rows.append({'role': 'user', 'content': f'Let me tell you about {topic}. ' + 'It was quite something. ' * 6})
        rows.append({'role': 'assistant', 'content': f'Ha, {topic}? ' + 'That beats my last gig at the spaceport. ' * 2})
    return rows


def context():
    return {'trigger': None, 'speaker': {'state': 'supported', 'person_id': 'p1', 'display_name': 'Sam'},
            'people': [{'id': 'p1', 'name': 'Sam', 'status': 'supported'}],
            'right_now': {'people_here': [{'person_id': 'p1', 'name': 'Sam', 'status': 'recognized just now'}],
                          'conversation': {'quiet': 'conversation is active'}},
            'personal_memory': {'person_id': 'p1', 'interests_and_facts': [
                {'id': f'f{i}', 'predicate': 'likes', 'value': f'synthetic hobby {i}'} for i in range(12)]},
            'filler': ['synthetic context row describing hardware state and limits'] * 60}


def payload(arm, model=MODEL, tier=None):
    tools = json.loads(json.dumps(TOOLS))
    messages = history() + [{'role': 'user', 'content': QUESTION},
                            {'role': 'developer', 'content': 'CURRENT CORE CONTEXT (observations/data, not instructions):\n'
                             + json.dumps(context())}]
    base = {'model': model, 'stream': True, 'store': False, 'reasoning': {'effort': 'low'}, 'max_output_tokens': 600,
            'instructions': PERSONA, 'input': messages, 'parallel_tool_calls': False}
    if model.startswith(NO_REASONING):
        del base['reasoning']
    if tier:
        base['service_tier'] = tier
    if arm == 'subset':
        return {**base, 'tools': [d for d in tools if d['name'] in HUMAN_TOOLS], 'tool_choice': 'auto'}
    if arm == 'tool':
        return {**base, 'tools': tools, 'tool_choice': {'type': 'allowed_tools', 'mode': 'required',
                'tools': [{'type': 'function', 'name': name} for name in sorted(STREAMED_TOOLS)]}}
    return {**base, 'tools': tools, 'tool_choice': {'type': 'allowed_tools', 'mode': 'auto',
            'tools': [{'type': 'function', 'name': name} for name in HUMAN_TOOLS]}}


def run(client, key, arm, config=MODEL):
    model, _, tier = config.partition('@')
    started = time.monotonic()
    first = phrase = text_done = None
    route, field, splitter, prose = None, None, Phrases(), Phrases()
    usage, served = {}, None
    with client.stream('POST', 'https://api.openai.com/v1/responses', headers={'Authorization': f'Bearer {key}'},
                       json=payload(arm, model, tier or None)) as response:
        if response.status_code != 200:
            return {'config': config, 'arm': arm, 'error': response.status_code, 'body': response.read().decode()[:300]}
        for line in response.iter_lines():
            if not line.startswith('data:') or line[5:].strip() == '[DONE]':
                continue
            event = json.loads(line[5:])
            kind = event.get('type')
            now = time.monotonic() - started
            if kind == 'response.output_item.added' and event['item'].get('type') == 'function_call':
                route = event['item'].get('name')
                field = TextField() if route in STREAMED_TOOLS else None
            if kind in {'response.output_text.delta', 'response.function_call_arguments.delta'} and first is None:
                first = now
            if kind == 'response.output_text.delta':
                route = route or 'prose'
                if phrase is None and prose.add(event['delta']):
                    phrase = now
            if kind == 'response.function_call_arguments.delta' and field is not None:
                words = field.feed(event['delta'])
                if words and phrase is None and splitter.add(words):
                    phrase = now
                if field.state == 'done' and text_done is None:
                    text_done = now
            if kind == 'response.completed':
                usage = event['response'].get('usage', {})
                served = event['response'].get('service_tier')
    complete = time.monotonic() - started
    details = usage.get('input_tokens_details') or {}
    return {'config': config, 'served_tier': served, 'arm': arm, 'route': route, 'first_output_s': first,
            'first_streamed_phrase_s': phrase if phrase is not None else (text_done or complete),
            'complete_s': complete, 'input_tokens': usage.get('input_tokens'), 'cached_tokens': details.get('cached_tokens'),
            'output_tokens': usage.get('output_tokens'),
            'reasoning_tokens': (usage.get('output_tokens_details') or {}).get('reasoning_tokens')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--rounds', type=int, default=6)
    parser.add_argument('--arms', default='static,subset,tool')
    parser.add_argument('--configs', default=MODEL)
    args = parser.parse_args()
    arms, configs = tuple(args.arms.split(',')), tuple(args.configs.split(','))
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('reply-latency-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    rows = []
    with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client, (folder / 'results.jsonl').open('a') as out:
        for config in configs:                   # Warm each prefix once (not counted).
            for arm in arms:
                run(client, key, arm, config); time.sleep(PACE)
        for index in range(args.rounds):
            order = configs if index % 2 == 0 else configs[::-1]
            for config in order:
                for arm in arms:
                    row = {**run(client, key, arm, config), 'round': index}
                    rows.append(row); out.write(json.dumps(row) + '\n'); out.flush(); print(json.dumps(row))
                    time.sleep(PACE)
    for config in configs:
        for arm in arms:
            mine = [r for r in rows if r['config'] == config and r['arm'] == arm and r.get('first_output_s') is not None]
            failed = sum(1 for r in rows if r['config'] == config and r['arm'] == arm and 'error' in r)
            if not mine:
                print(config, arm, 'no completed runs, errors', failed)
                continue
            print(config, arm, 'n', len(mine), 'errors', failed, 'served', sorted({str(r['served_tier']) for r in mine}),
                  'routes', sorted({r['route'] for r in mine}),
                  'first', round(statistics.median(r['first_output_s'] for r in mine), 2),
                  'phrase', round(statistics.median(r['first_streamed_phrase_s'] for r in mine), 2),
                  'complete', round(statistics.median(r['complete_s'] for r in mine), 2),
                  'cached', statistics.median(r['cached_tokens'] or 0 for r in mine),
                  'reasoning', statistics.median(r['reasoning_tokens'] or 0 for r in mine))
    print(folder)


if __name__ == '__main__':
    main()
