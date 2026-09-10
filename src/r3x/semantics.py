"""Small explicit model tool surface. Tool schemas validate syntax, not meaning."""


def schema(name, description, fields):
    import copy
    return {"type":"function", "name":name, "description":description, "strict":True,
            "parameters":{"type":"object", "additionalProperties":False,
                          "properties":{key:copy.deepcopy(value) for key,value in fields.items()}, "required":list(fields)}}


TEXT = {"type":"string"}
NULL_TEXT = {"type":["string","null"]}


TOOLS = (
    schema("introduce_person", "Interpret an explicit introduction. subject=self starts a provisional acoustic association only; other never enrolls the current speaker as the mentioned person. Use existing candidate ID when resolved, otherwise null. Never use unrelated next utterance as a name answer.",
           {"name":TEXT,"aliases":{"type":"array","items":TEXT},"subject":{"type":"string","enum":["self","other"]},"person_id":NULL_TEXT,"face_track_id":NULL_TEXT}),
    schema("record_claim", "Propose a lasting personal fact about an explicit subject. A property's meaning must stand independently of this conversation: who was just mentioned/addressed is conversational context, not biography. Names/introductions use introduce_person. Questions, jokes and their asides, insults, passing remarks, and generated fiction are not facts to save. Use the current source_turn_id; unresolved speakers remain candidate claims.",
           {"subject_id":TEXT,"subject_reference":{"type":["string","null"],"description":"For third_party, the explicit person name/alias in this utterance; must resolve uniquely. For self_report, null."},
            "predicate":TEXT,"value":TEXT,"kind":{"type":"string","enum":["self_report","third_party"]},"source_turn_id":TEXT}),
    schema("correct_claim", "Correct a specific prior claim using the current utterance; preserves its source/history. Only its supported source speaker or subject can correct it.",
           {"claim_id":TEXT,"value":TEXT,"source_turn_id":TEXT}),
    schema("retract_claim", "Retract a prior personal factual claim. Does not cancel voice enrollment or correct a person's name; use cancel_association for rejected identity associations.", {"claim_id":TEXT,"source_turn_id":TEXT}),
    schema("recall", "Read accepted current claims with who said them and source; null subject returns a bounded view. Candidate claims are not established facts.", {"subject_id":NULL_TEXT}),
    schema("cancel_association", "An identity correction or decline cancels provisional voice collection. Use the affected candidate person ID or track ID, never assume the current speaker is the mentioned person.",
           {"person_id":NULL_TEXT,"track_id":NULL_TEXT}),
    schema("rename_person", "Correct the display name of an explicitly resolved person; never assigns the current voice to that person. Requires the subject's supported voice or its current provisional introduction.",
           {"person_id":TEXT,"name":TEXT,"aliases":{"type":"array","items":TEXT}}),
    schema("forget_person", "Forget an explicitly identified person on request, including accepted recordings and dependent claims. Requires supported self identity; otherwise explain that the local management command is needed.", {"person_id":TEXT}),
    schema("ask_question", "Deliver one targeted clarification whose pending state begins only after the question is actually heard. No future utterance is automatically consumed as its answer.",
           {"text":TEXT,"addressee_id":NULL_TEXT}),
    schema("settle_question", "Mark a delivered pending question answered, declined, or superseded based on the meaning of this turn. Does not store a personal fact or confirm identity.",
           {"status":{"type":"string","enum":["answered","declined","superseded"]}}),
    schema("remain_silent", "End this turn without speech ONLY for clear external conversation/device announcements or an uninvited fragment. Do not use for a self-introduction, a correction to Rex, a memory request, or a conversational reply. Unknown speaker identity does not justify silence. No follow-up model call; no acoustic synthetic-voice claim.",
           {"reason":{"type":"string","enum":["other_addressee","device_speech","background_conversation","no_response_needed"]}}),
)


def grounded_tools(context):
    """Constrain identifier syntax to the current snapshot, independently revalidated on apply."""
    import copy
    definitions = copy.deepcopy(TOOLS)
    people = [p["id"] for p in context.get("people",[])]
    claims = [c["id"] for c in context.get("claims",[])]
    faces = [f["track_id"] for f in context.get("faces",[])]
    enrollments = context.get("enrollment",[])
    allowed = []
    for definition in definitions:
        name = definition["name"]
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
            if key=="claim_id": values = claims
            if key=="face_track_id": values = faces
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
