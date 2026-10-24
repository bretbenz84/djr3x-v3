"""The fixed meanings of the reply context's fields, kept in one place.

October 17 (reply context diet): about six thousand characters of explanatory text travelled
inside the per-request JSON on every model call, after the history, where nothing is cached.
The builders still attach each meaning under its field (so replay reports, scenario fixtures
and checks read the same context), but the hosted adapter strips any meaning found here from
the request and carries this guide once in the persona instructions, which are cached.

Entries are keyed by the field they describe; the values are the texts the builders attach.
A meaning not listed here stays in the request, so a new or changed text degrades to the old
behaviour rather than vanishing.
"""

GUIDE = {
    'right_now': ('Rex\'s sense of the moment, kept by code. Times are in words because they matter as '
                  'feelings: whether a moment has passed, whether someone is still around, whether a question '
                  'is still hanging. "Here" means still around, not necessarily in view; it never says who is '
                  'speaking.'),
    'right_now.conversation.his_earlier_lines': (
        'Points, jokes and apologies he already made: never make them again, and never bring back a mistake '
        'he already owned unless they do.'),
    'right_now.rex.looking': ('For your own awareness: with wide eyes, people stay in sight when your head turns. '
                              'Do not tell them where they sit in your sight unless they ask.'),
    'current_view': ('Latest fresh face evidence for who is visible now. Empty means no fresh detected face, not '
                     'proof the room is empty. Separate from speech-time faces; never authorizes speaker '
                     'attribution or enrollment.'),
    'recent_audience': ('Last person recognized as the private audience. Keeps their memories usable through '
                        'brief camera gaps; not proof they are visible now or that they spoke.'),
    'drive': ('Robot-relative bearings, positive = left. Face/object bearings come from the head camera '
              '(approximate, re-measured with the neck centered when a step runs). Radar bodies are moving '
              'bodies seen by the base radar ring: where to look, not who. speaker is where the last located '
              'voice came from, kept relative to where the base faces now as it turns (heard_seconds_ago is its '
              'age). Clearance is what base sensors report now; Rex remembers what the range sensors saw around '
              'him for about a minute (no floor plan or house map). path.direct_approach blocked_early means a '
              'straight approach will stop at an obstacle first.'),
    'drive.clearance_m': ('Nearest time-of-flight return in meters by direction, measured now. path_m is what the '
                          'body would drive into straight ahead (path_left_m, path_center_m and path_right_m say '
                          'which side of it); front_m is the nearest front return even beside the path; '
                          'left_m/right_m are the sides and rear_m behind. 3.5 or more means nothing within '
                          'range; null means not reporting. The sensors see objects, not what they are.'),
    'drive.radar_view': ('LD2450 radar ring on its own controller, all around the base. It tracks moving or '
                         'recently moving bodies only: a still person can vanish, and a body is not an identity.'),
    'drive.limits': ('max_move_m is the whole distance one plan may cover; long moves run as several legs '
                     'automatically and clearance still stops the base at obstacles.'),
    'drive (unavailable)': 'Rex has a wheeled base, but its controller did not connect at startup this run.',
    'place': ('Room recognition from the camera, not a map or a position. recognized: Rex knows this room. '
              'unsure: it may be one of looks_like; say so if it matters, never claim it. unfamiliar: Rex has '
              'moved and does not recognize the room. Unfamiliar, or unsure for a while: when people are here '
              'and it fits, ask once what room this is (a guess from looks_like is fine; do not ask again if it '
              'went unanswered) and use name_room when told, including a confirmation such as "yes, the '
              'workshop". learning: views of a just-named room are being collected; it is not learned until '
              'last_learning says learned. connects_to/connections: rooms Rex has gone between, not distances '
              'or directions.'),
    'camera_continuity': ('Similar images indicate a revisited view, not object identity. Newly visible is not '
                          'newly present. Commanded motion is known; uncommanded movement remains unknown. '
                          'Reappearance alone is not a reason to repeat a question or remark; inspect for '
                          'current evidence when a human reference or unresolved interest justifies it.'),
    'invitations': ('Only completed audible invitations are waiting. Review time does not mean rejection, '
                    'disinterest or an answer. Close explicitly and retain that it was asked. General scope is '
                    'impersonal only; private content stays audience scoped. Opening a new invitation explicitly '
                    'supersedes the previous one.'),
    'action_continuity': ('Purpose and actual executor/speech outcomes are separate. Interrupted work is '
                          'abandoned, never queued for automatic resumption. Follow the current user topic; only '
                          'choose a fresh action if still relevant. A completed move or capture does not mean its '
                          'answer was heard.'),
    'hypotheticals': ('"What if" questions he was heard asking after earlier long quiet stretches: spent. A new one '
                      'is a different premise, not a rewording.'),
    'your_hypothetical': ('Your "what if" from the last long quiet. When they take it up, play along: give your own '
                          'answer with conviction, argue it, escalate it, ask what they would do. It is make-believe, '
                          'never a fact or a plan. If they ignore it or talk about something else, drop it.'),
    'impersonation': ('Voices Rex can perform through the local cloning engine. self is the current speaker and '
                      'is usable only when ready is true (otherwise Rex needs to hear more of them in ordinary '
                      'conversation first). people are known voices on file (here: in the room, on an impression '
                      'beat); famous are target ids (the list is in the static context at the start of the '
                      'request). spontaneous_allowed covers cameos and impression beats. Parody is never a fact '
                      'about anyone.'),
    'visual_memory (each row)': ('Historical model interpretation, not current vision or verified personal fact. '
                                 'Do not infer a change from a new frame or rewording.'),
    'visual_novelty': ('Observed is not mentioned; mentioned is not heard. Identical pixels are not novelty, '
                       'different pixels are not proof of meaningful change. Unheard detail is not automatically '
                       'interesting. Choose a new conversational angle or silence, not merely new wording.'),
    'photo_memory': ('Named objects/pets and reference photos. Detector labels and tracks are uncertain, not '
                     'permanent identity. No person identity or ownership inferred.'),
    'photo_memory.catalog_scope': ('Only these entries are saved. A mentioned name absent here has NO saved '
                                   'photographic memory. Never apply its details to the sole existing entry.'),
    'photo_memory.detector_scope': ('Unidentified category/position hints only. No named identity or appearance '
                                    'evidence. Inspect fresh pixels and references before answering current pet '
                                    'identity or spatial comparisons.'),
    'pet_arrival': ('An animal came into view. The detector confuses dogs and cats; only a look against saved '
                    'photos can say which pet it is.'),
    'humor': ('Your own material with these people. running_gags landed before: a callback can be great, '
              'sparingly, never the same way twice. bits_that_flopped: leave them alone. bits_told_lately: do '
              'not tell them again.'),
    'agency_plan': ('The goal Rex chose for himself. Answer the human first; let the reply move the goal along '
                    'only when it fits naturally. Never recite it or force it.'),
    'reconsideration': 'Invitation review metadata only; agency plans own periodic advancement.',
    # Owner, October 22: closing the visor "effectively blinds him"; he should know it and make light of it.
    'visor_over_eyes': ('Your visor is shut over your eyes: you see nothing until it is raised. Faces and the room '
                        'are what you saw before it closed; never describe your surroundings as if you could see '
                        'them now. Now and then while it is shut, make light of your own sudden darkness ("Oh wow, '
                        'it\'s dark in here", "who turned out the lights?", "nobody move, I can\'t find you"), never '
                        'the same way twice. The joke is on your own situation, never on blind people or blindness. '
                        'You may ask them to raise it.'),
    'visor_over_eyes (yours to raise)': (
        'Your visor is shut over your eyes and you see nothing; faces and the room are what you saw before it '
        'closed. It has been down a while: whenever you want to see again you may raise it yourself '
        '(set_robot_pose with visor_normal), and lean into it in its text ("I kept it down for you, but I really '
        'wanted to see again. It was dark in here!"). Keep it shut if they asked you to keep it down. Joke about '
        'your own darkness, never about blind people or blindness.'),
    'claims': ('Saved facts about everyone, with ids for correct_claim and retract_claim. The facts of '
               'personal_memory\'s person are listed only there (interests_and_facts, same ids).'),
}

# Every guide text, for stripping from a request by value.
GUIDE_TEXTS = frozenset(GUIDE.values())
# Keys under which builders attach a fixed meaning.
MEANING_KEYS = frozenset({'meaning', 'clearance_meaning', 'catalog_scope', 'detector_scope'})


def render():
    """The guide as a persona section (cached with the rest of the instructions)."""
    lines = ['CONTEXT FIELD GUIDE. Fixed meanings of CURRENT CORE CONTEXT fields; the request JSON leaves them out:']
    lines += [f'- {field}: {text}' for field, text in GUIDE.items()]
    return '\n'.join(lines)


# Camera bookkeeping on every face that the model never reads (October 17: two face lists were
# 4,100 characters a request). Code keeps them; the request leaves them out.
FACE_DIAGNOSTICS = frozenset({'similarity', 'margin', 'detector_score', 'sharpness', 'brightness',
                              'embedding_available', 'learning_quality', 'track_first_seen', 'face_epoch'})


def slim(value):
    """A copy of a context with the guide's fixed meanings and face diagnostics removed (data stays)."""
    if isinstance(value, dict):
        face = 'track_id' in value and 'box' in value
        return {k: slim(v) for k, v in value.items()
                if not (k in MEANING_KEYS and isinstance(v, str) and v in GUIDE_TEXTS)
                and not (face and k in FACE_DIAGNOSTICS)}
    if isinstance(value, list):
        return [slim(v) for v in value]
    return value


def without_repeated_facts(context):
    """Saved facts already shown in personal_memory are not sent twice in claims (same ids)."""
    memory = context.get('personal_memory') or {}
    shown = {row.get('id') for row in memory.get('interests_and_facts') or () if row.get('id')}
    if shown and isinstance(context.get('claims'), list):
        context = {**context, 'claims': [c for c in context['claims'] if c.get('id') not in shown]}
    return context
