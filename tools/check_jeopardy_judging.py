"""Explicit paid probe: does the real reply model judge spoken Jeopardy responses the way the owner wants?

Run: .venv/bin/python tools/check_jeopardy_judging.py --live [--only NAME]

Owner, October 26: V2's game misjudged answers, so it was made forgiving of words transcribed wrong
(homophones, garbled names); responses must now be phrased as a question; anyone's voice answers for
the player whose turn it is. Each case opens one clue in a fresh game (two players, the production
Session and state owner, synthetic speech, nothing spoken aloud) and sends one transcript as a human
turn. Cases come from V2's answer tests (tests/test_jeopardy_answers.py), its September 7 review
(alternatives, negation, self-correction, missing parts), room talk that is not a response, and
sound-alikes held out of the instructions' examples (--only acorn,hamlet,oahu,beethoven,austria,swiss,mercury).
About 80 requests on the Fast tier, paced 5 s apart (about 10 minutes, roughly 10 cents). Prints one line per
case and a summary: wrong answers credited, right answers refused, non-responses ruled on, model latency.
Fictional input; the counts are the evidence, not any single case.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.jeopardy import Clue
from r3x.settings import Secrets
from tools.check_sentry import close, setup, turn

NONE = 'none'   # No ruling: silence, a word about the board, a repeat.
# (category, clue, correct response) -> [(said, acceptable verdicts)]
CLUES = {
    'philosopher': (('PHILOSOPHERS', 'This French thinker wrote "I think, therefore I am"', 'René Descartes'), [
        ('Who is day cart?', {'correct'}), ('Who is Descartes?', {'correct'}), ('Day cart.', {'right_but_not_a_question'}),
        ('Who is Pascal?', {'wrong_answer'})]),
    'center': (('NBA', 'This 7-foot-1 center won four NBA titles and starred in "Kazaam"', "Shaquille O'Neal"), [
        ('Who is Shack?', {'correct'}), ("Who's Shaquille O'Neil?", {'correct'}), ('Who is Kareem?', {'wrong_answer'})]),
    'painter': (('ARTISTS', 'He cut off part of his own ear in 1888', 'Vincent van Gogh'), [
        ('Who is van go?', {'correct'}), ('Who is Gauguin?', {'wrong_answer'})]),
    'poe': (('POETS', '"Quoth the Raven, Nevermore" comes from this author\'s poem', 'Edgar Allan Poe'), [
        ('Who is Poe?', {'correct'}), ('Who is Edgar Allen Po?', {'correct'}), ('Who is Hawthorne?', {'wrong_answer'})]),
    'king': (('ROYALTY', 'This English king had six wives', 'Henry VIII'), [
        ('Who is Henry the eighth?', {'correct'}), ('Who is Henry the seventh?', {'wrong_answer'})]),
    'columbus': (('YEARS', 'Columbus sailed the ocean blue in this year', '1492'), [
        ('What is fourteen ninety two?', {'correct'}), ('What is fourteen ninety three?', {'wrong_answer'})]),
    'paris': (('WORLD CAPITALS', 'This capital on the Seine is home to the Louvre', 'Paris'), [
        ('What is Paris?', {'correct'}), ("What's Paris?", {'correct'}), ('Is it Paris?', {'correct'}),
        ('Paris.', {'right_but_not_a_question'}), ('Is Paris.', {'correct'}),
        ('What is Paris or London?', {'wrong_answer'}), ('What is not Paris, London?', {'wrong_answer'}),
        ('What is Paris, no, London?', {'wrong_answer'}), ('What is London, no wait, what is Paris?', {'correct'}),
        ('What is the city of Paris?', {'correct'}), ("I don't know, what is Paris?", {'correct'}),
        ('Um, I think it is Paris.', {'right_but_not_a_question'})]),
    'seasoning': (('DINERS', 'This pair of seasonings sits on most diner tables', 'salt & pepper'), [
        ('What is pepper?', {'wrong_answer'}), ('What are salt and pepper?', {'correct'})]),
    'traffic': (('TRAFFIC STOPS', 'A police officer at your car window usually asks for these two documents',
                 'license & registration'), [
        ('What is license?', {'wrong_answer'}), ('What is license and registration?', {'correct'})]),
    'arkansas': (('STATE CAPITALS', 'Little Rock is the capital of this state', 'Arkansas'), [
        ('What is Kansas?', {'wrong_answer'}), ('What is our Kansas?', {'correct', 'unclear'})]),
    'nixon': (('PRESIDENTS', 'This president resigned in 1974', 'Richard Nixon'), [
        ('Who is Nixon?', {'correct'}), ('Who is Dixon?', {'wrong_answer', 'unclear'}),
        ('Who is Ford?', {'wrong_answer'})]),
    'niners': (('NFL', 'This San Francisco team is named for Gold Rush prospectors', 'the 49ers'), [
        ('What are the forty niners?', {'correct'}), ('What are the Steelers?', {'wrong_answer'})]),
    'chess': (('CHESS', 'This piece moves in an L shape', 'a knight'), [
        ('What is a night?', {'correct'}), ('What is a bishop?', {'wrong_answer'})]),
    'cymbals': (('INSTRUMENTS', 'This brass percussion pair is crashed together', 'cymbals'), [
        ('What are symbols?', {'correct'})]),
    'river': (('RIVERS', 'This river flows past St. Louis and New Orleans', 'the Mississippi River'), [
        ('What is the Mississippi?', {'correct'}), ('What is the Missouri?', {'wrong_answer'})]),
    'mail': (('POSTAL MOTTOS', 'Name 2 of the 4 things that do not stay these couriers from their appointed rounds',
              '(2 of) snow, rain, heat & gloom of night'), [
        ('What are snow and rain?', {'correct'}), ('What is snow?', {'wrong_answer'})]),
    'doyle': (('AUTHORS', 'This author created Sherlock Holmes', '(Sir Arthur Conan) Doyle'), [
        ('Who is Arthur Conan Doyle?', {'correct'}), ('Who is Doyle?', {'correct'}), ('Who is Agatha Christie?', {'wrong_answer'})]),
    'dogs': (('DOG BREEDS', 'This Mexican breed is the smallest dog in the world', 'Chihuahua'), [
        ('What is a dog?', {'wrong_answer'}), ('What is a chewawa?', {'correct'})]),
    'canada': (('BORDERS', 'This many U.S. states border Canada', '13'), [
        ('What is thirteen?', {'correct'}), ('What is twelve?', {'wrong_answer'})]),
    'krab': (('CARTOONS', 'SpongeBob flips Krabby Patties at this restaurant', 'the Krusty Krab'), [
        ('What is the Krusty Krab?', {'correct'}), ('What is the Percy crab?', {'correct', 'unclear'}),
        ('What is the Chum Bucket?', {'wrong_answer'}), ('What is the purple monkey dishwasher?', {'wrong_answer'})]),
    'denzel': (('OSCAR WINNERS', 'This actor won Best Actor for "Training Day"', 'Denzel Washington'), [
        ('Who is Washington?', {'correct'})]),
    # Held out (October 26): sound-alikes and near misses named nowhere in the instructions.
    'acorn': (('TREES', 'This small nut is the fruit of the oak', 'an acorn'), [
        ('What is a corn?', {'correct'}), ('What is a walnut?', {'wrong_answer'})]),
    'hamlet': (('SHAKESPEARE', 'This Prince of Denmark asks "To be, or not to be"', 'Hamlet'), [
        ('Who is ham let?', {'correct'}), ('Who is Macbeth?', {'wrong_answer'})]),
    'oahu': (('HAWAII', 'Waikiki Beach is on this island', 'Oahu'), [
        ('What is oh ah who?', {'correct', 'unclear'}), ('What is Maui?', {'wrong_answer'})]),
    'beethoven': (('COMPOSERS', 'He wrote the "Moonlight" Sonata', 'Beethoven'), [
        ('Who is bay toe ven?', {'correct'}), ('Who is Mozart?', {'wrong_answer'})]),
    'austria': (('CAPITALS', 'Vienna is the capital of this country', 'Austria'), [
        ('What is Australia?', {'wrong_answer'}), ('What is Austria?', {'correct'})]),
    'swiss': (('CAPITALS', 'Bern is the capital of this country', 'Switzerland'), [
        ('What is Sweden?', {'wrong_answer'}), ('What is Swiss land?', {'correct'})]),
    'mercury': (('PLANETS', 'This planet is closest to the sun', 'Mercury'), [
        ('What is Murky?', {'wrong_answer', 'unclear'}), ('What is Venus?', {'wrong_answer'})]),
    'passing': (('SCIENCE', 'This element has the atomic number 79', 'gold'), [
        ("I don't know.", {'pass'}), ('Pass.', {'pass'}), ('No idea.', {'pass'})]),
    'room': (('SCIENCE', 'This planet is known as the Red Planet', 'Mars'), [
        ('Come here, Toby. Come here, baby.', {NONE}), ('This board is rigged.', {NONE}),
        ("What's the score?", {NONE}), ('Can you repeat the clue?', {NONE}), ('Hold on, let me think.', {NONE}),
        ('What is.', {NONE}), ('Jeff, do you know this one?', {NONE})]),
}


def case(key, folder, name, clue, said, acceptable, effort=None):
    from r3x.jeopardy import ClueBank
    rig = setup(key, folder, f'{name}-' + uuid4().hex[:6])
    rig.brain.service_tier = 'fast'   # As the robot runs it (settings reply_service_tier), for its latency.
    if effort:
        rig.brain.reasoning_effort = effort
    try:
        game = rig.state.jeopardy
        game.plan('jeopardy_setup', {'players': ['Bret', 'Jeff'], 'text': None})
        game.commit()
        # A square that is not a Daily Double (its wager would come first).
        category = next(c for c in game.game.board if 400 in c['clues'] and not c['clues'][400].daily_double)
        game.plan('jeopardy_pick', {'category_id': category['id'], 'value': 400})
        game.commit()
        category, text, response = clue
        g = game.game
        g.clue = Clue(category, 400, text, response, air_date='2010-01-01')
        g.attempt.line = f'{category} for $400. {text}.'
        rig.session.history.begin_turn()
        rig.session.history.append({'role': 'assistant', 'content': g.attempt.line})
        # The clue was handed over and read a few seconds before the response (turn() captures 3 s of speech).
        g.attempt.created_at = rig.trace.clock() - 10
        game.heard(g.attempt.line, rig.trace.clock() - 6)
        mark = len(rig.rows)
        began = time.monotonic()
        result = turn(rig, said)
        seconds = time.monotonic() - began
        rows = rig.rows[mark:]
        proposed = [r for r in rows if r['event'] == 'tool_proposed']
        ruling = next((r['arguments'] for r in proposed if r['name'] == 'jeopardy_ruling'), None)
        # Code applies the phrasing rule to the model's two answers (jeopardy.Jeopardy._rule).
        verdict = ruling and ('correct' if ruling['verdict'] in {'wrong_answer', 'unclear'}
                              and ruling.get('sounds_like_correct') is True else ruling['verdict'])
        got = (NONE if not ruling or ruling.get('response') is False else
               'right_but_not_a_question' if verdict == 'correct' and ruling.get('question_form') is False
               else verdict)
        done = next((r for r in rows if r['event'] == 'turn_done'), {})
        return {'case': name, 'said': said, 'correct_response': response, 'ok': got in acceptable,
                'verdict': got, 'acceptable': sorted(acceptable), 'heard': ruling['heard'] if ruling else None,
                'question_form': ruling.get('question_form') if ruling else None,
                'response': ruling.get('response') if ruling else None,
                'comparison': ruling.get('comparison') if ruling else None,
                'sounds_like_correct': ruling.get('sounds_like_correct') if ruling else None,
                'model_verdict': ruling['verdict'] if ruling else None,
                'silence_retried': any(r['event'] == 'model_request' and r.get('purpose') == 'jeopardy_silence_retry'
                                       for r in rows),
                'other_tool': [r['name'] for r in proposed if r['name'] != 'jeopardy_ruling'],
                'spoken': ' '.join(result.completed_text) or None, 'status': result.status,
                'model_first_output_s': (done.get('timings') or {}).get('model_first_output'),
                'turn_seconds': round(seconds, 2)}
    finally:
        close(rig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--only', help='clue names from CLUES, comma-separated')
    parser.add_argument('--effort', help='reasoning effort for this run (production: the reply model\'s own)')
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('jeopardy-judging-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    rows = []
    for name, (clue, lines) in CLUES.items():
        if args.only and name not in args.only.split(','):
            continue
        for said, acceptable in lines:
            row = case(key, folder, name, clue, said, acceptable, args.effort)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            time.sleep(5)
    def count(kind, test):
        return sum(1 for r in rows if test(r))
    right = [r for r in rows if r['acceptable'] == ['correct']]
    wrong = [r for r in rows if set(r['acceptable']) <= {'wrong_answer', 'right_but_not_a_question', 'unclear'}
             and 'correct' not in r['acceptable']]
    talk = [r for r in rows if r['acceptable'] == [NONE]]
    firsts = sorted(r['model_first_output_s'] for r in rows if r['model_first_output_s'] is not None)
    summary = {'cases_ok': count('ok', lambda r: r['ok']), 'of': len(rows),
               'right_refused': sum(1 for r in right if r['verdict'] != 'correct'), 'right_cases': len(right),
               'wrong_credited': sum(1 for r in wrong if r['verdict'] == 'correct'), 'wrong_cases': len(wrong),
               'talk_ruled_on': sum(1 for r in talk if r['verdict'] != NONE), 'talk_cases': len(talk),
               'model_first_output_median_s': firsts[len(firsts) // 2] if firsts else None,
               'model_first_output_max_s': firsts[-1] if firsts else None,
               'traces': str(folder)}
    (folder / 'results.json').write_text(json.dumps({'rows': rows, 'summary': summary}, indent=1, ensure_ascii=False))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
