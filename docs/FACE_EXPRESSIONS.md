# What faces show — November 3, 2026

Owner: "In V2 we had smile detection and Rex would respond to it. That is missing in V3. Rex doesn't
currently have a good emotional awareness." This restores it for the reply model. It needs
`face_expressions_enabled = true`, the packages in `requirements-expressions.txt` and the model; all three are in place
on the robot's Mac since November 7 (owner approved the install), taking effect at his next start.

## What Rex does

- **A smile at his line.** Someone smiles while his line plays or within five seconds after it. His next line
  knows it landed and, unless what they just said is serious, is asked for one dry first-person touch ("My haircut
  material's clearly got range"), at most about every 75 seconds. Between those it is a fact he may use or ignore.
- **A smile out of nowhere.** A broad smile with no line of his behind it: he knows, does not know why, and
  may make one aside if it comes naturally.
- **Surprise and frowns.** Facts only: the model may play off them; nothing pushes it to.
- **One beat.** A look is in front of him until one heard line has had it, used or not, and for 60 seconds at most
  (V2's rule: "a smile is a moment, not a standing fact").
- **A smile counts as an answer.** It clears the count of unanswered lines, so he does not plan a
  "still there?" jab at someone grinning at him. A frown does not.
- **No new speech.** A look never makes Rex talk. It reaches the next reply, beat or plan, which he was going to
  produce anyway, with no extra model call.

The furrowed brow from V2 is left out: on the robot's own footage a resting brow read as furrowed about half the time.

## How it works

1. The camera's face pass (at most 2 per second) has SCRFD boxes already. `adapters/expression.py` runs MediaPipe
   Face Landmarker on an enlarged crop of each of the three largest faces at least 48 px wide. V2 ran it on the whole
   frame, whose short-range finder lost faces at conversational distance.
2. `expressions.py` decides. A look needs an absolute floor **and** a rise above that face's own resting level
   (V2's fix for mouths that read about .5 at rest with the camera looking up), held for two readings (about a second).
   A look with no line of his behind it must also reach V2's higher unprompted bar.
3. `present.py` keeps the moments and writes them into `right_now`: `conversation.rex_last_line.faces_at_it`,
   `expressions.faces`, `conversation.own_it`. The reply, the planner and the joke writer read the same picture.
   `own_it` adds a sentence to the reminder that closes the request, because the fact and field guide alone produced no
   visible use in the paid probe.

## Boundaries

A look never reaches speaker attribution, enrollment, identity, presence or saved memory (`tests/test_expressions.py`
checks a smile leaves the speaker deduction unchanged and never names a voice). Scores leave the reader only as these
looks, and the jaw is read only as a third of the surprise score. It is not saved beyond the private session log
(`camera_frame.face_details`). The model is told a look is what a face shows, not what someone feels, and that
their words come first. Images still may not be used to infer emotion (`describe_view`).

## Evidence

| Kind | Result |
| --- | --- |
| Synthetic | `tests/test_expressions.py`, 43 checks: rules above, camera failure paths, the speaker deduction unchanged |
| Real model, public photos | Broad smile .88-.89 at face widths from 130 down to 50 px; a straight-faced portrait .00-.01 |
| Real model, robot footage | October 22 sentry clip, 149 s, 63 px median face: 159 of 167 faces read; smile median .001, max .64; **no look fired** |
| Cost | About 8 ms per face (99th percentile 10 ms) on a 440 ms face pass; about 75 MB more resident in a process already holding the face models, torch, onnxruntime and mlx (mediapipe imports matplotlib); 2.7 s to load the first time, 0.4 s after |
| Paid probe, fictional input | `tools/check_expressions.py --live`; results in [status](IMPLEMENTATION_STATUS.md#what-faces-show--november-3) |

The public photos and clip have no labelled accuracy: the clip shows no false alarm, not that real smiles fire. Whether
Bret's and guests' smiles fire at the robot's distance and angle, and how far frowns and surprise can be trusted,
needs the robot. Frown and surprise did not fire on any saved picture, so they have no positive evidence at all.

## To check on the robot

1. Sit at your usual distance with a straight face for a minute: `grep expression_moment` on the session log
   should find nothing, and `expression_read.smile` should stay low.
2. Have him tease you and smile at it. The log should show `faces_at_it` in `right_now`, and his next line should
   show it about one time in three (paid probe, 2 of 6). A small polite smile may not fire; a real grin should.
3. Thresholds are in `expressions.py` (`FLOOR`, `RISE`, `UNPROMPTED`). The logged `expression_read` values are what to
   calibrate them against.
