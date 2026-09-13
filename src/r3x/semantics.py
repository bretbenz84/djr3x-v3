"""Small explicit model tool surface. Tool schemas validate syntax, not meaning."""


def schema(name, description, fields):
    import copy
    return {"type":"function", "name":name, "description":description, "strict":True,
            "parameters":{"type":"object", "additionalProperties":False,
                          "properties":{key:copy.deepcopy(value) for key,value in fields.items()}, "required":list(fields)}}


TEXT = {"type":"string"}
NULL_TEXT = {"type":["string","null"]}


TOOLS = (
    schema("set_initiative", "Only for an explicit request to stop unsolicited conversation (quiet), or an explicit invitation to resume initiating (available). Quiet still permits direct replies. This session-wide boundary lasts for this run and is not cleared by another person's speech or facing the camera. Never infer it from tiredness, grief, short answers or an external conversation. Quiet executes silently, without acknowledgment. Use current source_turn_id.",
           {"mode":{"type":"string","enum":["quiet","available"]},"source_turn_id":TEXT}),
    schema("introduce_person", "Interpret an explicit introduction, including a speaker giving the name of an existing person. Select face_track_id from visual evidence when clear; naming a shy friend keeps the current speaker separate. A face_status other than saved/already_saved means the face has NOT been enrolled; use a targeted clarification or closer look if needed. This is not a rename. subject=self starts a provisional acoustic association only; other never enrolls the current speaker as the mentioned person. Use existing candidate ID when resolved, otherwise null. Never use unrelated next utterance as a name answer.",
           {"name":TEXT,"aliases":{"type":"array","items":TEXT},"subject":{"type":"string","enum":["self","other"]},"person_id":NULL_TEXT,"face_track_id":NULL_TEXT}),
    schema("ask_face_question", "Ask a natural targeted name or closer-look question about one visible face. This targets a face, not necessarily the speaker. Another person may answer for them. Pending state begins only after delivery.",
           {"text":TEXT,"face_track_id":TEXT}),
    schema("reset_face_identity", "On an explicit correction that a visible face has been assigned the wrong person, remove that person's saved face references so a new introduction can rebuild them. Does not rename the person or change voice profiles. Never infer a correction from a low score.",
           {"person_id":TEXT,"face_track_id":TEXT}),
    schema("record_claim", "Propose a lasting personal fact about an explicit subject. A property's meaning must stand independently of this conversation: who was just mentioned/addressed is conversational context, not biography. Names/introductions use introduce_person. Questions, jokes and their asides, insults, passing remarks, and generated fiction are not facts to save. Use the current source_turn_id; unresolved speakers remain candidate claims.",
           {"subject_id":TEXT,"subject_reference":{"type":["string","null"],"description":"For third_party, the explicit person name/alias in this utterance; must resolve uniquely. For self_report, null."},
            "predicate":TEXT,"value":TEXT,"kind":{"type":"string","enum":["self_report","third_party"]},"source_turn_id":TEXT}),
    schema("correct_claim", "Correct a specific prior claim using the current utterance; preserves its source/history. Only its supported source speaker or subject can correct it.",
           {"claim_id":TEXT,"value":TEXT,"source_turn_id":TEXT}),
    schema("retract_claim", "Retract a prior personal factual claim. Does not cancel voice enrollment or correct a person's name; use cancel_association for rejected identity associations.", {"claim_id":TEXT,"source_turn_id":TEXT}),
    schema("recall", "Read accepted current claims with who said them and source; null subject returns a bounded view. Candidate claims are not established facts.", {"subject_id":NULL_TEXT}),
    schema("cancel_association", "An identity correction or decline cancels provisional voice collection. Use the affected candidate person ID or track ID, never assume the current speaker is the mentioned person.",
           {"person_id":NULL_TEXT,"track_id":NULL_TEXT}),
    schema("rename_person", "Only an explicit correction to the stored spelling/display name uses this tool. A self-introduction uses introduce_person, including existing names. Mentioning someone ELSE must never rename the current speaker to that other person. Requires the subject's supported voice or its current provisional introduction.",
           {"person_id":TEXT,"name":TEXT,"aliases":{"type":"array","items":TEXT}}),
    schema("forget_person", "Forget an explicitly identified person on request, including accepted recordings and dependent claims. Requires supported self identity; otherwise explain that the local management command is needed.", {"person_id":TEXT}),
    schema("ask_question", "Deliver one targeted clarification whose pending state begins only after the question is actually heard. No future utterance is automatically consumed as its answer.",
           {"text":TEXT,"addressee_id":NULL_TEXT}),
    schema("settle_question", "Mark a delivered pending question answered, declined, or superseded based on the meaning of this turn. Does not store a personal fact or confirm identity.",
           {"status":{"type":"string","enum":["answered","declined","superseded"]}}),
    schema("remain_silent", "End this turn without speech for an unfinished thought/thinking pause, clear external conversation/device announcements, or an uninvited fragment. Use unfinished_thought when a clause is still waiting for its action/object/destination or the speaker asks for time to think; do not ask them to finish. Explicit requests to stop unsolicited conversation use set_initiative(quiet) when available; addressee corrections require silence, never an acknowledgment. Complete introductions, ordinary questions, and replies in our conversation normally deserve speech. Unknown speaker identity does not justify silence. No follow-up model call; no acoustic synthetic-voice claim.",
           {"reason":{"type":"string","enum":["other_addressee","device_speech","background_conversation","no_response_needed","unfinished_thought"]}}),
)


def grounded_tools(context):
    """Constrain identifier syntax to the current snapshot, independently revalidated on apply."""
    import copy
    definitions = copy.deepcopy(TOOLS)
    if context.get('trigger')=='visual_arrival':
        definitions = [d for d in definitions if d['name'] in {'remain_silent','ask_face_question'}]
    elif context.get('trigger')=='conversation_lull':
        definitions = [d for d in definitions if d['name']=='remain_silent']
    people = [p["id"] for p in context.get("people",[])]
    claims = [c["id"] for c in context.get("claims",[])]
    faces = [f["track_id"] for f in context.get("faces",[])]
    enrollments = context.get("enrollment",[])
    allowed = []
    for definition in definitions:
        name = definition["name"]
        identity_targets = context.get("identity_action_targets", {}).get(name, [])
        if name in {"rename_person", "forget_person"} and not identity_targets:
            continue
        if name in {"ask_face_question","reset_face_identity"} and not faces:
            continue
        if name=="reset_face_identity" and not people:
            continue
        if not people and name in {"record_claim","rename_person","forget_person"}:
            continue
        if not claims and name in {"correct_claim","retract_claim"}:
            continue
        if not enrollments and name=="cancel_association":
            continue
        if not context.get("pending_question") and name=="settle_question":
            continue
        for key,spec in definition["parameters"]["properties"].items():
            values = None
            if key in {"person_id","subject_id","addressee_id"}: values = people
            if key=="person_id" and name in {"rename_person","forget_person"}: values = identity_targets
            if key=="claim_id": values = claims
            if key=="face_track_id":
                values = [context['visual_arrival']['face_track_id']] if context.get('trigger')=='visual_arrival' else faces
            if key=="track_id": values = [e["track_id"] for e in enrollments]
            if key=="source_turn_id": values = [context["source_turn_id"]]
            if name=="cancel_association" and key=="person_id": values = [e["person_id"] for e in enrollments]
            if values is not None:
                nullable = isinstance(spec["type"],list) and "null" in spec["type"]
                spec["enum"] = list(dict.fromkeys(values))+([None] if nullable else [])
        allowed.append(definition)
    return allowed


def validate(name, args):
    definition = next((s for s in TOOLS if s["name"] == name), None)
    if definition is None or not isinstance(args, dict):
        raise ValueError("UnknownTool")
    properties = definition["parameters"]["properties"]
    if args.keys() != properties.keys():
        raise ValueError("ToolFieldsMismatch")
    for key, spec in properties.items():
        value = args[key]
        if value is None and isinstance(spec["type"], list) and "null" in spec["type"]:
            continue
        if spec["type"] == "array":
            if not isinstance(value, list) or len(value) > 8 or not all(isinstance(v,str) and 0 < len(v.strip()) <= 120 for v in value):
                raise ValueError("InvalidAliases")
        elif not isinstance(value, str) or not value.strip() or len(value) > (1200 if key == "text" else 500):
            raise ValueError("InvalidToolText")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("InvalidToolEnum")
