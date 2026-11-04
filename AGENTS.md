# Working on DJ-R3X v3

## Scope and document authority

Read `README.md`, `DESIGN.md`, and the relevant portion of
`docs/INITIAL_TEST_PROGRAM.md` before implementation. Use `V2_REFERENCE.md` to
locate integration examples; inspect only the relevant v2 code and dependencies.

The user's current instructions take precedence over this guidance. Requirements
and proposals are distinguished in DESIGN.md. These documents authorize no work
beyond the user's request; a design discussion is not an instruction to deploy,
operate hardware, copy personal data, or implement the entire roadmap.

This repository implements the conversation core through slices 0–4, including
speaker evidence, camera presence, semantic tools, memory, replay, and maintenance.
Live group/device acceptance and held-out acoustic accuracy remain pending.
Do not describe planned tools, commands, tests, models, or runtime behavior as
implemented. Update status and evidence when implementation actually lands.

## Architectural boundaries

- One conversational owner interprets language and chooses replies or tools.
- One speaker tracker owns acoustic tracks and their identity hypotheses. Speaker,
  mentioned person, visible face, source direction, and addressee are distinct.
- One state owner validates and applies proposals. One output owner schedules
  speech and records actual delivery. Providers never speak on their own.
- No regex may convert arbitrary speech directly into a name, personal fact,
  answer to a pending question, or confirmed identity. Strict command fast paths
  such as stop/cancel remain useful; syntax validation does not infer meaning.
- The LLM cannot promote weak acoustic evidence into a verified identity. A name
  answer starts a hypothesis; repeated compatible audio supports enrollment.
- Mouth motion is excluded from ALL speaker-attribution and enrollment paths,
  including vetoes, fallbacks, derived confidence, and learning permission.
- Presence may complete a near-threshold voice (owner ruling, October 14). Only the
  state owner does it, and only with one recognized face in view, nobody else around
  for ten minutes, no other voice this visit, no in-clip disagreement, and that person
  as the voice's clear best match above a floor. The result is the separate state
  `inferred`. It never feeds voice or face learning, enrollment or identity
  administration, and a visible face alone never names a speaker.
- A voice clearly matched twice stays matched for the conversation (owner ruling,
  October 16): the state owner carries it through motion noise and a face too far
  to recognize while the clip still sounds most like that person above a floor, no
  other voice or recognized person came up in ten minutes, and nobody has been
  silent for five. Also `inferred`, with the same exclusions; other voices are never
  tied to that person.
- Someone introduced by another person is learned from the room (owner ruling, October 20):
  while that person has no saved voice or face, the state owner links the one unknown voice
  heard since shortly before the introduction (never the introducer's, nothing close to a
  saved voice) and pools views of the one face that is neither recognized nor resembling a
  saved face. Another unknown voice heard twice undoes the link; the voice still completes only through
  the enrollment chain. A newcomer's objection does not stop it; forgetting does.
- What a face shows (owner ruling, November 3) is for the conversation only: it reaches the picture of the
  present and the reply, never speaker attribution, enrollment, identity, presence or saved memory.
- Personal facts retain source turns, speaker attribution, subject, and status.
  An uncertain speaker does not silently become the mentioned person.
- Ordinary replies retain one primary model generation. Do not add a mandatory
  planner/classifier call or a metadata preamble before every spoken response.
- No model or device loads at import time. Every enabled capability declares its
  dependencies and has explicit start, cancellation, and close behavior.

## V2 reuse

V2 is at `/Users/bbenziger/djr3x-v2`. Treat it as reference material, not a runtime
dependency. Do not copy its config.py, requirements.txt, environment, startup chain,
interaction handlers, or global state into v3. The drive ESP32 firmware now lives in
v3 `firmware/djr3x_motion` (checks: `tools/run_firmware_checks.py`); edit and flash from
there, not V2's copy. Firmware edits and flashes still need the owner's request.

For each extraction, record source revision and working-tree status, behavior to
preserve, dependencies, adaptation, focused checks, and remaining live validation
in V2_REFERENCE.md. Check for uncommitted changes before assuming a source revision
or GitHub link contains the latest fix.

V2 documents contain historical and superseded sections. Prefer the current active
implementation plus evidence; investigate contradictions rather than silently
choosing a convenient old note. Existing tests can preserve useful cases without
preserving obsolete handlers or adding compatibility shims solely to satisfy them.

Do not modify v2 or its local data as an incidental part of v3 work. It is the
working baseline. A copied adapter must accept v3-owned dependencies and must not
reach back into v2 through sys.path changes, symlinked mutable state, or imports.

## Implementation discipline

- Prefer small typed Python modules and explicit interfaces. Use stable identifiers
  for people/tracks/turns; names are display and lookup data, not primary keys.
- Keep one active implementation per responsibility. Retire replaced code, settings,
  dependencies, startup loads, and tests that only exercise the retired machinery.
- Do not introduce a new backend, framework, service, or always-resident model
  without a demonstrated need and a measured resource/latency impact.
- Add configuration only for a current user choice or justified calibration.
  Each setting needs a consumer, default, purpose, and validation. No speculative
  switches, historical behavior modes, or silent provider substitutions.
- Correct state through explicit versioned transitions. Avoid module-level mutable
  session globals and hidden work launched by imports or callback side effects.
- Provider errors become structured results. Never acknowledge a save, action, or
  completed speech before its executor reports the relevant success.
- Proactive participation is part of the conversational core, designed and evaluated
  with replies, attribution and memory; read docs/PROACTIVE_CONVERSATION.md for the
  September 12 direction. Delight and mood improvement are goals; emotional awareness
  guides sensitivity, not automatic energy matching. Shared arrival/lull behavior,
  feedback, memory opportunities and observation inputs are implemented;
  docs/IMPLEMENTATION_STATUS.md separates completed software from live acceptance
  and future coding. Documentation alone does not authorize
  implementing additional stages or operating hardware.
- Keep unrelated feature expansion frozen during core evaluation. New perception,
  external feeds and hardware behaviors need scoped work rather than opportunistic
  implementation; route eventual opportunities through the same conversation owner.

## Data boundaries

Use v3 scratch state under the ignored `local/` directory. Add ignore rules before
creating databases, personal recordings, evaluation audio, exports, or credentials.
Never commit secrets, voiceprints, personal memory, private logs, or model weights.
Track configuration examples with placeholders and synthetic test fixtures instead.

Any authorized import uses independent copies, a source digest, explicit person-ID
mapping, and the model namespace. No writable connection to v2 people.db or rex.db.
Use a SQLite-consistent snapshot for a live database rather than copying only its
main file while WAL writes may be pending. Copies do not make a profile ground truth.

Keep accepted original voice audio with provenance. Re-embedding previews changes
before applying them and preserves model namespaces. Forgetting a person or removing
bad voice data must cover recordings, derived references, and in-memory proposals.

## Verification and communication

Use the v3 environment and its eventual documented runner. V2 checks must use the
v2 venv and `tools/run_lean_checks.py` with modules in separate processes; never
substitute a v3 environment or unrestricted v2 discovery for that runner.

Ordinary automated checks use temporary databases, fake clocks/providers, and
blocked network/audio/serial. Live checks are explicit and labeled separately.
Never operate robot hardware just to make an automated test pass.

Test observable contracts: original utterance, decision, write, delivery, restart,
correction, cancellation, and rejection. Do not add tests that only mirror regexes.
Keep the replay entry point shared with production; a separate mock brain can
prove its own behavior while leaving the real program broken.

Report which checks used synthetic data, real models on saved audio, live providers,
or real hardware. Synthetic embeddings do not prove Bret/Jeff discrimination. Logs
without waveforms cannot establish capture or overlap accuracy.

Measure both wrong-person attribution and abstention. A tracker that never assigns
anyone must not pass as accurate. Report latency stages, median/tail, model calls,
loaded models, memory use, and playback issues without inventing missing values.

When a slice is complete, update its acceptance evidence and outstanding limitations.
Do not leave a permanent second conversation loop or a fallback that restores the
retired semantic handler. Keep documentation concise and consistent across files.
