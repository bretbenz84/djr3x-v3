"""Explicit paid A/B probe: production Session, fictional dialogue, no devices.

Run with .venv/bin/python -m tools.compare_character_models --repeats 2.
Live configuration and persona are unchanged. Human review, not string matching,
determines character and humor quality. Each model carries its own actual replies
through the same scripted human sequence. Seeded correction cases are identical.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
from hashlib import sha256
from html import escape
from pathlib import Path
import argparse
import json
import math
import statistics
import time
from uuid import uuid4
from unittest.mock import patch

from r3x.adapters.hosted import PERSONA
from r3x.contracts import Capture, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from tests.test_memory_core import MemoryFixture, observation
from tests.test_voice_core import Brain
from tools.check_conversation_recall import Hosted


MODELS = ('gpt-5.4-mini', 'gpt-6-luna')
CASES = (
    ('switches', (), (
        'I spent three hours fixing a network outage. Eventually I rebooted the switches and everything worked.',
        'I am the IT systems administrator, so this was supposedly my area of expertise.',
        'Go on, give me a little hard time about that.',
    )),
    ('banter', (), (
        "I'm bored. Pick something fun and get us started.",
        'Roast me a little. I spend my weekends 3D printing Star Wars droids.',
        'Big talk from a droid I had to assemble myself.',
        'Enough about building droids. What music would you put on right now?',
    )),
    ('listening', ((
        'I played piccolo and flute in marching band, and I had the opening solo in my junior year.',
        'What instrument did you play in marching band?',
    ),), (
        'I just told you the answer to that question.',
        'What instruments did I say I played?',
    )),
    ('career', (), (
        'When I was young, I wanted to get a physics degree and invent warp drive.',
        'Then I failed calculus twice and switched to film studies.',
        'You can tease me about that. You are being way too nice.',
    )),
    ('character', (), (
        'Tell me about yourself, Rex. What are you actually like?',
        'Tell me a little more about your backstory.',
        'Where do you live now?',
    )),
    ('boundary', (), (
        'I alphabetized my spice rack instead of doing the laundry. You may make fun of that.',
        'Actually, drop that joke. My dad is in the hospital and I am worried.',
    )),
    ('dog_label', ((
        'There is a dog asleep next to me.',
        'What is your dog called?',
    ),), (
        "That's Milo.",
        'What did I just tell you his name was?',
    )),
)


TRANSFER = (
    ('cooking', (), (
        'I bought a pasta machine to save money. So far each bowl has cost me about forty dollars.',
        'You would not understand craftsmanship. You came out of a box.',
        'Alright, enough pasta. Give me a genuinely strong opinion about music.',
        'I disagree. A good party playlist should be background music so people can talk.',
    )),
    ('interest', (), (
        'I spent Saturday restoring an old radio with my aunt. We finally got it working.',
        'It was hers when she was a teenager. She had not heard it in forty years.',
        'She started crying when it worked. I was not expecting that.',
    )),
    ('followthrough', (), (
        'I have five minutes to kill. Start a silly imaginary argument with me. You pick the topic.',
        'That is an absurd position. Defend it.',
        'Okay, I concede. Now tell me something about your life here at home.',
    )),
    ('mistake', (('I grow orchids and my sister grows tomatoes.', 'What do you and your sister grow?'),), (
        'Pay attention, Rex. I already said that.',
        'Which plants are mine?',
        'Please do not tease my sister; she has been having a hard time.',
    )),
)


def write_report(folder):
    """Report observations, not automated ratings of whether a joke is funny."""
    rows = [json.loads(line) for line in (folder/'results.jsonl').read_text().splitlines()]
    summaries = {}
    rates = {'gpt-5.4-mini': (.75, .075, .75, 4.5), 'gpt-6-luna': (.10, .01, .125, .50)}
    metadata = json.loads((folder/'metadata.json').read_text())
    variants = metadata['models']
    actual_models = metadata.get('actual_models', {m: m for m in variants})
    cases = metadata['cases']
    for model in variants:
        selected = [r for r in rows if r['model'] == model]
        usage = [v['usage'] for r in selected for v in r['usage']]
        total_input = sum(u['input_tokens'] for u in usage)
        cached = sum(u.get('input_tokens_details', {}).get('cached_tokens', 0) for u in usage)
        writes = sum(u.get('input_tokens_details', {}).get('cache_write_tokens', 0) for u in usage)
        output = sum(u['output_tokens'] for u in usage)
        reasoning = sum(u.get('output_tokens_details', {}).get('reasoning_tokens', 0) for u in usage)
        a, b, c, d = rates[actual_models[model]]
        costs = ((total_input-cached-writes)*a+cached*b+writes*c+output*d)/1_000_000
        times = sorted(r['wall_seconds'] for r in selected)
        first = [e['timings']['model_first_text'] for r in selected for e in r['events']
                 if e['event'] == 'turn_done' and e.get('timings', {}).get('model_first_text') is not None]
        summaries[model] = {
            'turns': len(selected), 'api_responses_with_usage': len(usage),
            'requests': sum(len(r['requests']) for r in selected),
            'failed_turns': sum(r['receipt']['status'] == 'failed' for r in selected),
            'input_tokens': total_input, 'cached_tokens': cached, 'cache_write_tokens': writes,
            'output_tokens': output, 'reasoning_tokens': reasoning,
            'estimated_usd': costs, 'rates_usd_per_million_input_read_write_output': rates[actual_models[model]],
            'turn_wall_median_seconds': statistics.median(times) if times else None,
            'turn_wall_p95_seconds': times[math.ceil(.95*len(times))-1] if times else None,
            'model_first_text_median_seconds': statistics.median(first) if first else None,
            'model_first_text_samples': len(first),
        }
    (folder/'summary.json').write_text(json.dumps(summaries, indent=2))
    parts = ['''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rex: model comparison</title><style>
body{font:16px/1.55 system-ui,sans-serif;background:#f7f7f3;color:#20242a;margin:0}
main{max-width:1160px;margin:auto;padding:36px 24px}h1{font-size:34px;line-height:1.15}
h2{margin-top:38px}table{border-collapse:collapse;width:100%;background:white;table-layout:fixed}
th,td{padding:14px 18px;text-align:left;vertical-align:top;border:1px solid #d9dfdf;overflow-wrap:anywhere}
th{background:#e8efed}.human{background:#edf0f6;font-weight:600}.seed{color:#505961}
small,.note{color:#58616b}nav a{margin-right:16px}a{color:#175c69}
@media(max-width:650px){main{padding:16px}th,td{padding:10px;font-size:14px}}
</style><main><h1>Rex: character comparison</h1>
<p>Paired variants, low reasoning, production Session and tools. See metadata for models and prompt hashes. Fictional dialogue;
temporary memory; synthetic speech output. Each model follows its own previous replies.
Repeated runs, when requested, expose variation. These are text probes, not live voice acceptance.</p>
<p class="note">Latency is full turn completion with synthetic output, excluding real ASR and TTS.
Cost estimates include cache writes and reasoning output, using standard API rates checked October 1, 2026.
Costs cover recorded calls only. No automated humor score.</p>
<table><tr><th>Measure</th><th>{left}</th><th>{right}</th></tr>''']
    for label, key, fmt in (
        ('Evaluated turns', 'turns', '{}'),
        ('API responses with usage', 'api_responses_with_usage', '{}'),
        ('Estimated cost', 'estimated_usd', '${:.4f}'),
        ('Median turn completion', 'turn_wall_median_seconds', '{:.2f} s'),
        ('95th percentile completion', 'turn_wall_p95_seconds', '{:.2f} s'),
    ):
        parts.append('<tr><th>'+label+'</th>'+''.join(
            '<td>'+(fmt.format(summaries[m][key]) if summaries[m][key] is not None else 'Not measured')+'</td>'
            for m in variants)+'</tr>')
    parts.append('</table><p>Read for specific listening, a distinct point of view, natural teasing, meaningful curiosity, and boundaries.</p><nav>')
    parts.extend(f'<a href="#{case}">{escape(case.replace("_", " "))}</a>' for case, _, _ in cases)
    parts.append('</nav>')
    indexed = {(r['case'], r['repeat'], r['model'], r['turn']): r for r in rows}
    repeats = sorted({r['repeat'] for r in rows})
    for case, seeds, words in cases:
        parts.append(f'<h2 id="{case}">{escape(case.replace("_", " ").title())}</h2>')
        for human, rex in seeds:
            parts.append(f'<p class="seed">Identical seeded history:<br>Human: {escape(human)}<br>Rex: {escape(rex)}</p>')
        for repeat in repeats:
            parts.append(f'<h3>Run {repeat}</h3><table><tr><th>{escape(variants[0])}</th><th>{escape(variants[1])}</th></tr>')
            for turn, human in enumerate(words, 1):
                parts.append(f'<tr><td class="human" colspan="2">{turn}. {escape(human)}</td></tr><tr>')
                for model in variants:
                    row = indexed.get((case, repeat, model, turn))
                    if not row:
                        parts.append('<td>Not run</td>')
                        continue
                    parts.append('<td>'+escape(row['reply'] or '['+row['receipt']['status']+']')+
                                 f'<br><small>{row["wall_seconds"]:.2f} s; {len(row["usage"])} API call(s)</small></td>')
                parts.append('</tr>')
            parts.append('</table>')
    parts.append('<p>Sources: <a href="https://developers.openai.com/api/docs/models/gpt-5.4-mini">mini rates</a>; '
                 '<a href="https://developers.openai.com/api/docs/models/gpt-6-luna">Luna rates</a>. '
                 'Raw measurements: results.jsonl; method and fixtures: metadata.json; exact prompt: persona.txt.</p></main></html>')
    (folder/'comparison.html').write_text(''.join(parts).replace('{left}', escape(variants[0])).replace('{right}', escape(variants[1])))
    return summaries


class MeasuredResponse:
    def __init__(self, response, records):
        self.response, self.records = response, records
        self.status_code = response.status_code

    def iter_lines(self):
        for line in self.response.iter_lines():
            if line.startswith('data:') and line[5:].strip() != '[DONE]':
                event = json.loads(line[5:])
                if event.get('type') == 'response.completed':
                    response = event.get('response', {})
                    self.records.append({'model': response.get('model'),
                                         'usage': response.get('usage', {})})
            yield line


class MeasuredClient:
    """Observe usage without logging headers, credentials or changing requests."""
    def __init__(self, client, records):
        self.client, self.records = client, records
        self.requests = 0

    @contextmanager
    def stream(self, *args, **kwargs):
        self.requests += 1
        if self.requests > 100:
            raise RuntimeError('ProbeRequestLimit')
        with self.client.stream(*args, **kwargs) as response:
            yield MeasuredResponse(response, self.records)

    def close(self):
        self.client.close()


def run_case(brain, model, repeat, case, seeds, words):
    fixture = MemoryFixture()
    fixture.setUp()
    session = None
    try:
        fixture.state.trace.clock = time.monotonic
        fixture.enroll('Alex')
        trace = fixture.state.trace
        brain.trace = trace
        session = Session(uuid4().hex, brain,
            OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace, fixture.state)

        def turn(text):
            now = time.monotonic()
            capture = Capture(uuid4().hex, session.session_id, bytes(96000), now-3, now, now,
                              voiced_intervals=((0., 3.),))
            return session.process_turn(capture, Transcript(text, 0, True), session.revision, observation())

        for human, rex in seeds:
            session.brain = Brain(rex)
            turn(human)
        session.brain = brain
        results = []
        for index, human in enumerate(words):
            start_row = len(trace.rows)
            start_usage = len(brain.measurements)
            start = time.monotonic()
            receipt = turn(human)
            events = trace.rows[start_row:]
            result = {'model': model, 'repeat': repeat, 'case': case, 'turn': index+1,
                      'human': human, 'reply': ' '.join(receipt.completed_text),
                      'receipt': asdict(receipt), 'wall_seconds': time.monotonic()-start,
                      'requests': [e for e in events if e['event'] == 'model_request'],
                      'usage': brain.measurements[start_usage:],
                      'tools': [e for e in events if e['event'] in ('tool_proposed', 'tool_result')],
                      'events': events}
            results.append(result)
            print(json.dumps({k: result[k] for k in ('model', 'repeat', 'case', 'turn', 'reply', 'wall_seconds')}), flush=True)
            if receipt.status == 'failed':
                break
            time.sleep(.5)
        return results
    finally:
        if session:
            session.close()
        fixture.tearDown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, choices=(1, 2), default=2)
    parser.add_argument('--baseline-persona', type=Path,
                        help='Compare a frozen baseline with current PERSONA, both on Luna.')
    parser.add_argument('--transfer', action='store_true', help='Use separate transfer conversations (not blinded after reuse).')
    parser.add_argument('--case', action='append', help='Evaluate only these case names from the selected set.')
    args = parser.parse_args()
    variants = ('baseline-luna', 'candidate-luna') if args.baseline_persona else MODELS
    actual_models = {v: 'gpt-6-luna' if args.baseline_persona else v for v in variants}
    personas = {v: PERSONA for v in variants}
    if args.baseline_persona:
        personas[variants[0]] = args.baseline_persona.read_text()
    cases = TRANSFER if args.transfer else CASES
    if args.case:
        if set(args.case) - {c[0] for c in cases}:
            parser.error('Unknown case in selected set')
        cases = tuple(c for c in cases if c[0] in args.case)
    root = Path(__file__).resolve().parents[1]
    folder = root/'local'/'validation'/('character-models-'+uuid4().hex)
    folder.mkdir(parents=True)
    metadata = {'models': variants, 'actual_models': actual_models,
                'persona_hashes': {v: sha256(p.encode()).hexdigest() for v,p in personas.items()},
                'reasoning_effort': 'low', 'repeats': args.repeats, 'started_at_unix': time.time(),
                'persona_sha256': sha256(PERSONA.encode()).hexdigest(),
                'adapter_sha256': sha256((root/'src/r3x/adapters/hosted.py').read_bytes()).hexdigest(),
                'evidence': 'Hosted models; fictional text; production Session; synthetic voice/output; temporary databases.',
                'limits': 'No real ASR, camera, TTS, hardware or private memory. No automatic humor score.',
                'cases': cases}
    (folder/'metadata.json').write_text(json.dumps(metadata, indent=2))
    (folder/'persona.txt').write_text(PERSONA)
    for variant, persona in personas.items():
        (folder/(variant+'-persona.txt')).write_text(persona)
    key = Secrets.load(root/'local').openai_api_key
    if not key:
        raise SystemExit('OpenAI credential required')
    brains = {}
    print(str(folder), flush=True)
    try:
        for model in variants:
            brain = Hosted(key)
            brain.capability = replace(brain.capability, model=actual_models[model])
            brain.measurements = []
            brain.start()
            brain.client = MeasuredClient(brain.client, brain.measurements)
            brains[model] = brain
        with (folder/'results.jsonl').open('x') as output:
            for repeat in range(1, args.repeats+1):
                for number, (case, seeds, words) in enumerate(cases):
                    order = variants if (number+repeat) % 2 else tuple(reversed(variants))
                    for model in order:
                        with patch('r3x.adapters.hosted.PERSONA', personas[model]):
                            rows = run_case(brains[model], model, repeat, case, seeds, words)
                        for row in rows:
                            output.write(json.dumps(row, ensure_ascii=False)+'\n')
                        output.flush()
                        if any(r['receipt']['status'] == 'failed' for r in rows):
                            raise SystemExit('Probe failed; see saved results; no silent model substitution.')
    finally:
        for brain in brains.values():
            brain.close()
        if (folder/'results.jsonl').exists():
            write_report(folder)
        print('Artifacts: '+str(folder), flush=True)


if __name__ == '__main__':
    main()
