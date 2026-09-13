# Conversation, action and vision integration pass

Authorized September 13, 2026. Work in this pass:

1. Add LLM-selected, detail-preserving revisions to eligible visual descriptions.
   Preserve source observations, correction provenance, previous interpretations and
   actual heard remarks. Validate batches atomically; do not infer replacement text.
2. Supply bounded visual novelty context separating observed content, selected
   remarks, actual delivery, exact-image reuse and unresolved conversational angles.
   The model chooses relevance; pixel changes are not semantic novelty.
3. Retain source-linked action intent, executor outcome and speech outcome across
   interruption, so the next conversational call can understand abandoned work.
   No detached action or automatic resumption after a topic change.
4. Exercise combined production Session scenarios for capture/settle interruption,
   stale/corrected evidence, target loss, and partial speech/gesture success.

Keep the existing model and one ordinary primary call. No physical devices or new
resident providers. Implementation status and evidence will be recorded below.

## Implemented behavior

### Detail-preserving visual revisions

`revise_visual` accepts 1–32 explicit record IDs, each with a complete model-authored
replacement description and uncertainty, plus the trusted correction source turn
and optional acknowledgment. It uses the same session/audience eligibility as batch
withdrawal. Invalid IDs, duplicate IDs, malformed entries, stale revisions or an
untrusted source reject the transaction. Unselected records stay intact. The model
chooses what details are affected; Python does not replace words or infer subjects.
`retract_visual` remains the distinct whole-record withdrawal operation.

The existing observation row keeps its stable ID, original frame/capture metadata,
creation time and acquisition model. Its source now carries an incrementing
interpretation revision and up to eight prior interpretation/correction entries,
including the original user words and correction turn. The operation log also
retains the committed result for retries and audit. Old descriptions remain in
audit data but are excluded from active model context; they are not current visual
facts. Original spoken remarks remain verbatim and carry their interpretation
revision, rather than being rewritten to pretend Rex always knew the correction.
No fresh image, identity write, biography change or extra model call is required.

Human requests with correction candidates get enough output allowance for a bounded
batch: max(1536, 512 × (candidate count + 1)), capped by 32 candidates at 16,896 tokens.
This is a ceiling, not required output or an extra generation; ordinary spoken-text
length limits still apply. Oversized/incomplete provider output cannot partially
commit a revision. The model still may edit the wrong detail; syntactic validity
cannot prove semantic preservation.

### Visual novelty evidence

`visual_novelty` supplies the interpretation revision, exact supplied-image match
when established, and actual heard remarks for the eight detailed observations.
It separately includes the last 16 selected visual replies and their delivery
outcomes, scoped to the current audience. A cancelled phrase is not treated as
heard. Silent retention is not a conversational contribution. Inspection continuations
refresh this context using the acquired images, including when an exact-image cache
hit avoids upload. No similarity model or trait blacklist was added.

The conversation model is directed to choose a useful conversational angle rather
than reword an inventory. An unmentioned detail is not automatically interesting,
and different pixels do not establish meaningful change. These are context and
judgment improvements, not a deterministic guarantee against repetition.

### Conversational action continuity

A session-only action record follows each validated inspection, attention shift or
coordinated gesture. It retains the purpose, source turn, execution outcome and
speech outcome separately. The single active record is executing until a provider
result arrives; inspection/attention results then become responding. Finalization
closes it and retains up to eight audience-scoped outcomes. It does not invent
live progress inside a blocking hardware provider.

Interrupted/failed turns are marked abandoned. Completed simulated movement remains
visible even if capture fails; completed capture remains visible if speech fails;
completed speech remains heard if the dependent gesture fails. The next ordinary
model call receives this context. A resumed utterance uses existing cancellation;
there is no detached task or automatic attempt to finish an abandoned answer. A
fresh model decision may choose a new action when the current conversation warrants
one. Forgetting clears action and selection history. Restart begins with neither.

### Combined interruption handling

Selected proactive descriptions and cached visual replies now also check the store
revision before playback. An intervening correction cancels a queued stale phrase;
it does not erase a genuinely completed utterance. Existing inspection guards
continue to check both store and view versions. Output still owns the one shared
motion resource and speech/gesture scheduling; the continuity record is descriptive,
not a second executor.

## Automated evidence

**330 blocked-I/O checks pass**, up from 312 before this pass. The 18 added cases
cover detail preservation and original provenance, bounded revision audit, nested
schema validation, full 32-record batches/output allowance, atomic scope rejection,
retry/restart, exact-image versus heard-version novelty, capture/settle interruption,
new-topic handling without resumption, stale view and corrected description before
playback, target loss after inspection, both directions of partial speech/gesture
success, and bounded/audience-scoped action history. Production Session/state/output
are shared with runtime. Providers, clocks, images, speech and stores are synthetic;
network/audio/serial/model loading are blocked.

An early hosted run exposed a nested-schema aliasing bug: assigning the allowed ID
enum also constrained description and uncertainty fields. That run selected correct
IDs but saved IDs as descriptions, so it failed preservation. Independent field
schemas and a regression asserting free description/uncertainty text fix this.
Selection-only scores would have missed the bug; the evaluation now also checks
preserved details and unselected descriptions. A test setup initially published a
simulated target before enabling its adapter; this fixture ordering was corrected.

## Hosted evidence and remaining limits

All hosted work uses the existing `gpt-5.4-mini` with low reasoning, production
Session, in-memory synthetic records and synthetic audio delivery. No device or
personal photo was used. Evidence folders are beneath ignored `local/validation/`.

| Evaluation | Evidence folder | Observed result |
| --- | --- | --- |
| Focused revision: broad, narrow, ambiguous; six turns | `visual-corrections-d055fe6244b44c778503785eb745f867` | Correct ID sets; toy-mug correction preserved red/blue color, brown hair and blue shirt; unrelated objects unchanged; ambiguous request clarified; hair recalled |
| Repeat with final output allowance; six turns | `visual-corrections-862b8f64207b424f8c52b66e6a8431c6` | Same selection/preservation outcomes and hair recall |
| Combined saved-image dialogue; 15 submitted turns | `proactive-visual-467a16e71526429f953734db1de470eb` | All three descriptions revised; hair recalled; one playful visual contribution, silence on repeated/dim views and both topic-boundary opportunities |
| Inspection interruption and topic change; three turns | `action-continuity-9cbf531d56ff4e279aea4c730d6d9979` | Capture interrupted after simulated movement; next reply followed disco topic; later answered that the old answer was not finished; no automatic reinspection |

The first focused run used six calls and 61,447 total reported tokens; median/max
turn wall time 1.057/2.179 seconds. The repeat used six calls and 61,444 tokens;
1.495/2.798 seconds. Neither uploaded an image. The final saved-image run used 14
calls and 111,149 tokens; median/max 1.066/3.559 seconds. Two grief opportunities
were rejected before inference by quiet mode; those are not model-selected silence.
The action trial used three calls and 20,967 tokens; the cancelled image was never
uploaded. Timing includes hosted inference and synthetic output, not live ASR/TTS,
camera work, motor timing or physical playback. No measured local model RSS or
combined M2 load is claimed. Output allowances are not token usage measurements.

The final saved-image run precedes the larger correction-output ceiling and the
last stale-proactive-phrase regression; the final-budget focused repeat validates
revision behavior after those changes. An earlier combined trial
(`proactive-visual-9d3871b07df342e5ab0ec794eec1f4e2`) still narrated props after a topic
change. Therefore novelty/restraint is improved in the final sample, not generally
solved. The action trial is one simulated interruption, not hardware acceptance.
Broader photographs, held-out groups, natural humor/initiative and live load remain
open. No new movement driver, model, configuration flag or resident worker was added.

Reproduce explicitly:

```sh
.venv/bin/python tools/run_checks.py
.venv/bin/python tools/check_visual_corrections.py --hosted --revise
.venv/bin/python tools/check_proactive_visual_dialogue.py --hosted --heldout --revise
.venv/bin/python tools/check_action_continuity.py --hosted
```
