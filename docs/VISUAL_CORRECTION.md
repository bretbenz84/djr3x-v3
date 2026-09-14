# Correction across related visual memories

Current scope: [implementation checklist](IMPLEMENTATION_STATUS.md). Dated sections
below preserve the evidence and limitations of their respective checkpoints.

Implemented September 13, 2026. `retract_visual` now accepts an explicit list of
`observation_ids`, replacing the single-ID input. The conversation owner chooses
all descriptions affected by the user's correction. There is no Python semantic
matching, shared-word expansion, inferred person association or trait blacklist.

The primary human-turn context includes up to 32 compact correction candidates in
the current session and audience scope (stable IDs for recognized people, track IDs
for unknown faces). This covers the entire
bounded visual store, including records outside the eight-item detailed recall
window. Each candidate contains description, uncertainty, creation time and source
references. No additional image or model call is required. Proactive turns do not
offer correction tools or receive the extra candidate list.

The executor validates the source utterance, store revision, eligibility and 1–32
unique selected IDs. One invalid, stale or out-of-scope ID rejects the whole batch.
The transaction withdraws all selected records and advances the revision once;
unselected records remain active. Existing operation IDs make retries idempotent.
The operation retains its correction source turn and a result for each selected
record. A spoken acknowledgment is queued only after success. History receives
the executed result, so later replies can distinguish withdrawal from camera failure.
The hosted schema uses bounded arrays; duplicate rejection is executor-owned. An
initial schema using `uniqueItems` received HTTP 400; removing that keyword allowed
the otherwise same request to execute. Failed attempts are not semantic evidence.

This is **whole-description withdrawal**, not editing one attribute or saving a
replacement. A record containing both an incorrect object label and useful hair
color loses both when withdrawn. Source observations remain retained for audit,
but retracted records leave recall, correction selection and exact-image reuse.
The LLM must not promise a replacement was saved. Partial-description revision and
cross-session/person matching remain separate work; this does not correct biography
or identity. A broad correction only affects records the LLM actually selects.

## Automated evidence

**312 blocked-I/O tests pass**, including ten new correction tests and the updated
single-record regression. They cover multiple related IDs, unrelated preservation,
old records beyond the recall window, cross-session/audience rejection, atomic
failure, revision/trusted-source checks, cancellation, retry, restart, invalid lists,
retirement of the single-ID schema, per-record outcomes and production-Session
acknowledgment after commit in one generation. Tests use synthetic descriptions,
temporary databases and synthetic audio; no devices or network are used.

## Hosted evidence

Run explicitly:

```sh
.venv/bin/python tools/check_visual_corrections.py --hosted
.venv/bin/python tools/check_proactive_visual_dialogue.py --hosted --heldout
```

The focused test seeds three puppet-set mug descriptions, a distinct kitchen cup
and eight unrelated objects. The three target records are older than the detailed
recall window. It tests a broad correction, a blue-only correction, and an ambiguous
request, each followed by recall. Expectations are not supplied to the model.
Without `--hosted`, the focused command only writes a review plan.

Two hosted runs using the unchanged `gpt-5.4-mini` low-reasoning provider selected:

| Request | Observed result in both runs |
| --- | --- |
| Withdraw all mistaken puppet-set mug descriptions | All three target IDs withdrawn; kitchen cup and eight other records retained |
| Withdraw only the blue puppet-set description | Only the blue target ID withdrawn |
| “That thing you described is wrong” | No writes; asked which observation needed correction |

The six semantic selections matched the intended sets, with clarification manually
reviewed. Follow-up summaries were sometimes incomplete or gave unsupported ordering;
this is selection evidence, not comprehensive recall-quality acceptance.

Evidence folders under ignored `local/validation/`:

- `visual-corrections-33f7e284fca041b2924bca74883349e0`: six model calls, no image
  inputs, 55,120 reported total tokens; turn median/max 1.795/2.341 seconds.
- `visual-corrections-e45fd20555b941d5ab1844ac364a40fb`: six model calls, no image
  inputs, 55,383 reported total tokens; turn median/max 1.326/4.235 seconds.

These runs precede the final addition of executed correction results to history;
that addition is exercised by the subsequent saved-image conversation below.
Synthetic records/presence and synthetic audio use production Session/state/output.
No microphone, camera, actual audio playback, personal images or robot was operated.
Timings exclude real ASR/TTS/playback and do not establish live performance.

The saved-image conversation `proactive-visual-d0969449430a4b359b0293e37c04116d`
withdrew both independently saved related descriptions, fixing the earlier one-record
failure. It then inaccurately described missing hair recall as inability to make
out the earlier image. This motivated retaining the executed correction outcome
in conversation history. Repeated visual riffs remain unrelated open work.

Final saved-image rerun with executed-result history:
`proactive-visual-7e60ba3203884b3f883512dc85d7344d`. All three red/dim/blue
interpretations were withdrawn together, with no related record left active. Rex
then said it did not remember the hair color rather than claiming it could not see
it. The generic request to send the image again is still awkward for a robot; this
harness has no inspection adapter. The run used 14 model calls, 101,501 total reported
tokens, with turn median/max 1.326/2.475 seconds. Two serious-context opportunities
were rejected before inference because the earlier request activated quiet mode;
these do not count as model-selected restraint. The correction itself used one
primary call and no image upload. These small trials do not prove reliable selection
for ambiguous people, unrelated scenes sharing appearance, or live group usage.

### Core integration follow-on

Detail-preserving `revise_visual` is now implemented alongside whole-record withdrawal.
It retains correction provenance and useful unchanged details. Visual novelty context
and action continuity now distinguish observations, selected remarks, heard speech
and abandoned work. **330 blocked-I/O checks pass**, with focused hosted preservation
and simulated interruption evidence. See the [core integration pass](CORE_INTEGRATION_PASS.md)
for current behavior and remaining limits; earlier partial-edit deferrals describe
the preceding slices.
