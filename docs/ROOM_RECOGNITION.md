# Room recognition, naming and exploration — October 11, 2026

Owner request: room exploration and mapping, using V2's room models, so that in an
unfamiliar room Rex asks what room it is and remembers it. Owner choices: V2's local image
model, import V2's taught rooms, "mapping" means rooms plus which rooms connect (no
navigation), and exploring through Rex's existing goals.
[Extraction record](../V2_REFERENCE.md#room-recognition--october-11-2026).

**Status, October 31:** on the robot since October 16. He confuses the living room and the
bedroom, even standing still, and how often he names the right room is unmeasured
([robot runs](#on-the-robot-october-1629)).

## What Rex does

- **Knows the room.** About every 1.5 s, one camera frame becomes a 512-number
  fingerprint from V2's MobileCLIP-S2 model, compared with saved views of each known room
  (up to 15 per room). He settles on a room only when most of the last five frames agree.
  The room can change only after the base has moved, or after about 15 s of the camera
  insisting (he was carried). In a recognized room with the wheels still, he checks every
  6 s instead.
- **Asks in an unfamiliar room.** After moving, eight frames in a row that match nothing
  make the room *unfamiliar*. When people are present and it fits, Rex asks once what room
  this is. He may also ask, offering a guess, when he has been *unsure* between rooms for a
  while.
- **Remembers the answer.** `name_room` (from the person's own words, never a guess)
  creates the room or adds views to a known one. Rex then collects up to eight views, at
  least 3 s apart, within a minute. With three or more he has learned the room and adopts
  it; with fewer the new name is dropped and he can say so. He never claims to have
  memorized it before that.
- **Corrections.** `correct_room`: "this isn't the hallway" drops the belief only; rename;
  forget (removes all of the room's views and connections).
- **Connections.** Moving from one room he recognized to another within 15 minutes counts
  as a connection, with a count of how often. Rex can talk about which rooms lead where.
  These are not distances, directions or a floor plan, and "go to the kitchen" does not
  exist.
- **Exploring.** A new goal kind, `explore`, for an unfamiliar room or an invitation to look
  around: two to four short drives or turns, each followed by a look and one specific
  opinion, with at most one early "what room is this?" The planner chooses it; it is not a
  separate mode and it never takes over the conversation.

- **Everyday views (October 31).** The same fingerprints, with no room name, tell how many of the
  last fourteen days he has seen a view like the one in front of him: four or more is *everyday*
  (his usual surroundings, not news), one to three *seen before*, none *new* once there are three
  days of history. A sample is kept at most every two minutes while the wheels are still, not again
  when that view is already stored today, and for 30 days; the rooms' saved views count on the days
  they were captured. A stored view at .80 or more is the same view (October 31: two different rooms'
  saved views reach .81 at most). This run's own samples never make a view familiar. It adds no model;
  matching 620 stored views (a full fourteen days) took a median 15 ms on the robot's M2 (synthetic vectors, model idle), once every 10 s. Not yet run on the robot; `view_familiarity` events log each
  change with the best similarity, to check the .80 line.
  [Familiarity](IMPLEMENTATION_STATUS.md#familiarity-what-he-keeps-saying-everyday-views-and-time-apart--october-31).

## Kept from V2

V2 thresholds measured on this robot in July: a confident call needs ≥0.80 and a 0.04 lead
over the next room, or ≥0.86 when only one room is known. The 0.68 floor marks
"unfamiliar". Belief needs a 5-frame majority; the room is unfamiliar after 8 misses, and
the belief is dropped after 16. A room's gallery grows only from in-band frames (0.70–0.78)
that clearly belong to the believed room, and only when two or more rooms are known. These
are V2's field repairs: a lone or lagging gallery had absorbed another room's views.

Changed for V3:
- The model runs in ONNX, not PyTorch.
- Rooms live in the core SQLite store with cascade deletes.
- Words are interpreted by the conversation model, not V2's regex answer capture.
- A face wider than a quarter of the frame stands in for V2's "person covering 35%" check
  (V3 tracks faces, not bodies).
- Rooms Rex was carried between are not counted as connected.
- "Don't drive in this room" and object novelty were not requested and are not ported.

## Model, parity and cost

`tools/export_place_model.py` exports `encode_image` from V2's open_clip checkpoint, run
with V2's environment read-only. The result is `local/models/places/mobileclip-s2.onnx`,
SHA-256 `bf9d116d…4c935392`, 144 MB.

V3 reproduces open_clip's preprocessing in numpy. OpenCV's own resize had shifted
room-to-room similarities by up to 0.022, half of the decision margin. On 14 saved robot
frames, V3's fingerprints match V2's PyTorch pipeline at cosine 1.0000. The largest
pairwise difference is 0.0007, so imported V2 vectors are directly usable.

Measured on this robot (Apple M2, 16 GB), model only:

| Measure | Value |
| --- | --- |
| Load time | 0.2 s |
| Time per 1080p frame | 0.25–0.28 s |
| Memory added | about 300 MB (ONNX Runtime's memory arena off; 525 MB with it) |
| Matching against 45 views | about 1 ms |
| Network calls, cost | none |

Re-measured October 15 in a fresh process on the same M2 (synthetic 1280×720 frame): about
470 MB resident after loading (model plus runtime), 0.26 s of one core per frame, so about
17% of one core at the 1.5 s pace and about 4% at the 6 s settled pace.

Not yet measured: CPU contention with ASR, faces and objects during a live conversation,
and the effect on reply latency.

**October 15: it had never run.** The camera's frame hub accepted at most three consumers
(faces, scene, objects), so `places` was refused with `VisualConsumerCapacity` at every start
since October 11, with the setting on; in session `2f85db5c` Rex answered "Do you know what
room this is?" with "I can't tell". The hub now takes four (`vision.CONSUMERS`).

## Imported V2 rooms

`tools/import_v2_places.py` takes a read-only SQLite backup snapshot of V2
`data/places.db` (snapshot SHA-256 `1808063d…8126c`). It imported dining room, workshop and
living room, 15 views each, with new IDs and `import_ref`. The mapping is recorded in
`imports`. Before the import, cross-room similarity peaked at 0.75: no contaminated
galleries.

Scoring September frames saved by V3 (likely after V2 taught these rooms):
- Object-memory frames: workshop at 0.72–0.76, with a 0.11 lead.
- Face-distance frames: living room 0.71 vs workshop 0.69.

All of these are *unsure*, below the 0.80 confident bar. A likely but unconfirmed cause is
the wide-angle lens fitted September 27. Expect Rex to ask, or offer a guess, the first time
in each room. Telling him adds fresh views that make the room recognizable.

By October 29 none of the imported views remained (see below).

## On the robot (October 16–29)

Reviewed October 31 from the session logs and the store, read only. Nothing records which room he
was really in, so accuracy is not measured.

- **Running.** After the October 15 camera-hub fix, 48 runs logged 7,527 room readings: 42%
  confident, 56% unsure, 2% matching nothing. No run reached *unfamiliar*, so he never asked on
  his own what a room was. Rooms came up only when someone asked.
- **Taught by voice.** October 16 (`03bc4e1f`): the owner drove him around and said "This is the
  bedroom", then the dining room and the living room. Each was learned with 8 views. The bedroom
  was new; the two imported rooms had been scoring unsure. The new dining-room views resembled the
  living room at .90, which is noted but not refused (as V2 did). October 17 (`76184646`): asked
  which room he was in, he could not tell; told "living room", he learned it again.
- **Answers.** October 22 (`1072c10a`): asked, he said the workshop. October 24 (`bc2ec5d9`): his
  startup hello said he had "the living-room shift covered" two seconds before the camera settled
  on the workshop, and the owner corrected him.
- **Living room and bedroom confused while standing still.** In the October 26–27 Jeopardy run
  (`0c8be385`, 31 minutes, the wheels never moved) his room changed 20 times between the two,
  sometimes 4 s apart, with the top two scores often under .03 apart. Both rooms took two new
  views from that one spot. Each change was also saved as a connection: the living room–bedroom
  link reads 23 trips, most of them from this run. Without wheel motion, a change should take
  the 10-reading carried rule and not count as a connection, so the recognizer was being told he
  might be moving. Why is not yet established.
- **Saved views drifted.** Each room keeps its newest 15 views. By October 29 no imported view
  remained, and only the dining room kept any of its taught views. Everything else had been added
  automatically while he believed he was in that room, the way V2 saw one room's views creep into
  another's. That may explain the confusion above; it is not established.
- **Connections saved:** living room–bedroom 23, living room–dining room 6, dining room–bedroom 2.
- **Exploring.** One explore goal was planned with recognition running (October 16, `3af18a12`),
  for a room he was unsure of.

## Checks

- **Synthetic:** 13 contract checks in `tests/test_places.py` with fake encoder, clock and
  store (2 more need numpy and run outside the blocked runner). They cover:
  - thresholds, majority and motion freeze
  - the drive-callback move, carried flips and unfamiliar/lost states
  - close-person skipping and lone-gallery growth
  - naming, learning, failure and connections
  - corrections and cascades, tool availability, the import reader and preprocessing
- **Real model on saved frames:** the parity and scoring above.
- **Real hardware:** the robot runs above (recognizing, naming, answering, connections).
- **Not done:** accuracy against the room he is really in, across lighting and people; Rex
  asking about an unfamiliar room on his own; an explore goal driven to the end. The import
  reader was checked on a synthetic database; the real import was run once.

Enable with `place_recognition_enabled = true` (requires `--camera` and the model file).
