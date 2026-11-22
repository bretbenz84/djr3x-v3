# Room-to-room navigation plan — November 22, 2026

Owner request, after run `8cece04a`: Rex held a book, went through a doorway and down the hall,
then failed "come forward a little, go to your right and go into the living room". Asked for a
room-to-room navigation system with no costly lidar: pictures of each room in every direction,
the on-device room model, sensible links and directions between rooms, range-sensor readings as
an extra layer, maps grouped by building, the layout learned mostly by himself, and the reply
model describing what is in each room.

**Status: plan only. Nothing in this document is built.** Each slice needs the owner's go-ahead.
Sources: the run's session log, the room tables in `local/core.sqlite3` (read only), the code,
and web research listed under [What works elsewhere](#what-works-elsewhere).

## What happened in run `8cece04a` (12:09–12:14 AM)

| Asked | What Rex did | Result |
| --- | --- | --- |
| "Hold this." | Took the book. | Worked. |
| "Bring it down the hall." | Asked the hosted picture model where "the hallway" was, then rolled 4.05 m toward it, re-checking with 11 hosted looks. | Worked. |
| "Come forward a little, go to your right and go into the living room." | Forward 0.7 m, right 95°. The picture model said the living room was on the left of his view, past a doorway, so he turned 32° back left. Then the "drive toward it" step ended at once, 0.0 m travelled, and he announced "Living room." | Did not enter. |
| "You didn't go forward. You need to come into the living room." | The picture model saw the living room to his **right**, but marked the look low-confidence (blurred), so it counted as not seen. He then searched by turning **left** in three steps (about 200° in all), saw the kitchen, the front door and the hall he had come down, and gave up. | Wrong way, stuck. |

Three causes, all in how he drives, none in the wheels or sensors:

1. **A room is treated as an object.** He counts a thing as reached when it fills 60% of his
   view. That suits a chair. A room seen through its doorway fills the view from outside it.
2. **The search always turns left** unless the reply model says otherwise. It said "right" on the
   first request and nothing on the second. The low-confidence sighting on his right was thrown
   away rather than used to pick the side.
3. **Nothing tells him where the living room is.** Every trip starts from "can I see it in this
   picture?" When the room is not in the picture he spins.

## Was the room model used?

It ran the whole time (97 readings, about one every 1.5 s while moving). Its answer reached the
reply model as a line of text. **The driving never used it**: not to pick a direction, not to
decide he had arrived. And its answers were mostly wrong or unsure:

- **Workshop:** right, after 35 s of "unsure".
- **Hallway:** called it the **dining room** (scores .80–.86) from 80 s on. No room named
  "hallway" exists. It then saved five views from the hall and its far end as new dining-room
  views, so the dining room's fingerprint is turning into the hallway's.
- **Living room** (after the owner drove him in by gamepad): 100 s of "unsure", living room
  .73–.79 against bedroom .70–.78. He never recognized it, and the reply model was still being
  told "dining room" at shutdown. That is where "I'm in the dining room, so the living room is
  the next one over" came from.

## What the saved room fingerprints are worth

- **Four rooms** are saved: workshop, bedroom, living room, dining room. No hallway, kitchen or
  entry.
- **None of the 60 saved views was taught.** Each room keeps its newest 15, and every one was
  added automatically while he believed he was in that room. The views the owner taught by voice
  in October have all been pushed out. The living room's are a month old.
- **Fifteen views do not cover a room.** Testing each saved view against the other 14 of its own
  room: 9 of 60 would be recognized, 50 would be "unsure", 1 would be called another room. A view
  of the same room from another spot or angle typically scores about .77, under the .80 bar.
- **Everyday snapshots agree.** Of 159 unlabelled views kept over three weeks, 73% are "unsure",
  21% recognized, 6% unfamiliar. None is recognized as the living room.
- **The saved directions are unusable.** Each view stores a heading, but it is degrees from
  wherever he pointed when switched on, so it means something different every run. The compass
  is fitted and reporting, and V3 does not read it.
- **Why he never asks.** He asks only after eight readings in a row that match no room at all
  (under .68). In this house any view scores .70–.77 against some saved room, so the hall and
  the kitchen read as "unsure", never "unfamiliar". Two readings of 97 fell under .68 in this run.
- **Why a wrong belief sticks.** A belief is dropped only by a run of "unfamiliar" readings, or
  replaced by a confident other room. "Unsure" readings, however many, leave it standing.
- **The connections are wrong.** Living room–bedroom reads 23 trips (he flickered between them
  standing still in October). Workshop–dining room reads 4, which are trips into the hall.

So the owner's suspicion is right: he rarely asks, and the fingerprints are not well defined.

## What works elsewhere

Web research, November 22. Figures are the sources' own; none was re-measured here.

| Idea borrowed | Shown by | What they report |
| --- | --- | --- |
| **Teach a route by driving it once.** On the repeat the wheels and gyro do the driving and the camera only corrects heading. | [Krajník 2018](https://arxiv.org/abs/1711.05348); [Dall'Osto, Fischer, Milford 2021](https://arxiv.org/abs/2010.11326) | 7–10 cm repeat error indoors on a 2 kg robot with a 320×240 camera. Ran on a Raspberry Pi with pictures as small as 32×12, kept every 0.2–0.3 m, and tolerated 20% wheel error. Fails when started far off the route, and has no recovery of its own. |
| **The big model picks the destination; code drives.** | [Mobility VLA, Google DeepMind 2024](https://arxiv.org/abs/2407.07775) | From a recorded tour the model chose the goal and a graph of tour pictures did the driving. With the right goal, arrival was 100%. Asked to steer directly, the model scored 0%. |
| **A fingerprint built for telling places apart.** | [AnyLoc 2023](https://arxiv.org/abs/2308.00688); [EigenPlaces 2023](https://github.com/gmberton/EigenPlaces) | Indoors, a whole-picture CLIP fingerprint (the kind Rex uses) found the right place 43–59% of the time; purpose-built ones 64–96% on the same tests. |
| **Decide over a run of looks, and keep a "not in my map" answer.** | [SeqSLAM 2012](https://openslam-org.github.io/openseqslam.html); [FAB-MAP 2008](https://ori.ox.ac.uk/media/4688/ijrr_2008_fabmap.pdf) | Sequences beat single pictures; look-alike places are scored against every other place rather than a fixed bar. |
| **Range sensors say where a place is; the camera says which.** | [Kortenkamp & Weymouth 1994](https://aaai.org/?p=42770); [Zender & Kruijff 2007](https://www-live.dfki.de/fileadmin/user_upload/import/2454_zender_kruijff07-aaaisss.pdf); [Topp & Christensen](https://faculty.cc.gatech.edu/~hic/hic-papers/toppTransitions.pdf) | Passing a narrow opening makes a doorway. One spin gives a room's size and shape well enough to notice a room change. (They used a laser.) |
| **Indoor compass as a fingerprint, not a north.** | [Bachmann 2004](https://researchr.org/publication/BachmannYP04) | Off by up to about 16° in an ordinary room, more beside steel. The error at one spot is steady. |
| **A map per building.** | [Robart patent 2016](https://patents.google.com/patent/US9527212B2/en); [ORB-SLAM3](https://arxiv.org/abs/2007.11898) | Compare with every saved map while starting a temporary one; no good match means new; join maps on a recognized revisit. A long carry makes "somewhere else" likelier, and the person may simply say so. |
| **Names from people.** | Topp & Christensen; [Hemachandra & Walter 2015](https://ttic.edu/ripl/publications/hemachandra15a.pdf) | A name belongs to where the robot stood. Ask on entering somewhere new, weigh the question against the interruption, and remember having asked. |
| **Open-plan rooms are groups of spots.** | [Hydra 2022](https://arxiv.org/abs/2201.13360); [Clio 2024](https://arxiv.org/abs/2404.13696) | Automatic room-splitting fails in open plans. Robot vacuums have the person draw the line. |

Looked at and left out:

- **Mapping from the camera alone.** It loses track on blank walls and on turning in place, which
  is how Rex turns ([study](https://arxiv.org/abs/2307.12326)).
- **Driving by a trained model** (ViNT and relatives). A 2026 real-world test reports frequent
  collisions and confusion between look-alike places ([test](https://arxiv.org/abs/2603.25937)).
- **3D scene maps** (ConceptGraphs, Hydra, OK-Robot). They need a depth camera.

Not established by anyone found, so this plan measures it rather than assuming it:

- a room outline from eight single beams and two small depth grids (published work used lasers);
- whether views kept per direction beat views pooled per room;
- the speed of a purpose-built fingerprint model on his M2;
- how the compass behaves in this house, beside his own motors.

## Design

A map of **spots and the ways between them**, not a floor plan. Research calls this a
topological map. It needs no lidar because it never has to know where every wall is: only where
he has stood, what each spot looks like in each direction, and how he got from one to the next.

### Words

- **Place:** a building. Home, Dad's house, work. Each has its own map. (The existing `places`
  tables in the code hold rooms; they become the rooms of the place "home".)
- **Room:** a name people use: living room, kitchen, hallway.
- **Open space:** rooms that share one floor with no wall between, such as living room, dining
  room and kitchen.
- **Spot:** a point on the floor where Rex has turned all the way round and looked. A small room
  has one. A hall has one at each end. An open space has one per named room.
- **Passage:** a way he has actually travelled from one spot to the next.

### What a spot remembers

Eight looks, 45° apart (his lens sees 90°, so neighbours overlap). For each look:

- the fingerprint from the on-device model;
- which way he was facing, in the house's own directions (below);
- a small picture kept on his own disk under `local/`;
- one line from the hosted picture model on what is there ("couch and TV", "doorway to the
  hall"), made once, so the reply model can talk about rooms and choose them by content;
- the compass reading, taken while the motors are idle.

And once per spot, from the same turn: the **outline** the range sensors saw, a distance every
few degrees all the way round.

A full look-around takes about 25 s. It replaces today's "collect eight views in a minute".

### Knowing where he is

- Compare what he sees with every spot-and-direction of the current place, not with 15 loose
  views per room. The best match gives the spot **and** which way he faces.
- Judge a spot against all the others over a short run of looks, not one look against a fixed
  .80 bar. "None of them" is one of the possible answers.
- Add what the wheels say. He can only be at a spot reachable from where he just was, given how
  far he rolled. A hallway cannot become the dining room without travelling there. Standing
  still, the answer cannot flicker.
- Use the outline to break ties between look-alike rooms.
- "Lost" is an honest answer. Lost, he looks around; still lost, he asks.
- Being carried (view changes, wheels still) starts the search over, as today.

### Getting somewhere

"Go to the living room" becomes:

1. **Route:** the chain of passages from his spot to a spot in that room.
2. **Each passage:** turn to the direction it leaves in, roll the distance it took last time
   with today's obstacle handling, and aim at the opening with the range sensors. Tiny grey
   pictures kept every quarter metre when the passage was taught nudge his heading back onto
   the taught line. The camera never sets speed or distance, and a poor match is ignored.
3. **Arrived** when the spot he expected is the one he sees, not when something fills the view.
4. **Off the route or blocked:** look around, find himself, re-route. A shut door is reported as
   a shut door.
5. The hosted picture search stays as the fallback for things the map does not hold.

Normal trips then need no hosted looks. Yesterday's 4 m hall trip used 11.

**Spoken directions.** In "forward a bit, then right into the living room", the last part is a
destination and goes by the map. Where the map has no route yet, the spoken side decides which
way he searches, and "arrived" means the room model says living room.

### Learning the map

One mechanism, used three ways:

- **A house tour** (recommended start, about 10–15 minutes per building). The owner drives him
  with the gamepad. At each stop the owner says "this is the kitchen" and Rex does a look-around.
  The driving between two stops becomes the passage: direction out, distance, doorway or open
  floor, direction in. The tour is a goal of the existing planner, like exploring, not a
  separate program mode.
- **Everyday driving.** Any trip, by gamepad or by himself, records the same trail and
  strengthens or adds passages. A passage that fails repeatedly is trusted less.
- **Asking.** When he has come through a doorway into somewhere that matches nothing, he asks
  once, "Where am I?", and does a look-around on the answer. He remembers having asked about
  that spot. Names always come from the person's words, never from his own guess.

Nothing is added to a room's fingerprint automatically any more. Views enter the map only from
a look-around at a named spot.

### Open spaces

In the big room, what he **sees** from the kitchen is the living room. So a room there is where
he **stands**, set by the spot, not by what is in view. Spots in one open space are joined by
"open floor" passages with no doorway, and he may say "the living room, by the dining table".
Across open floor he can usually see where he is going, so today's look-and-drive works and the
spot tells him where to stop.

### Range-sensor layer

From each look-around's outline: typical distance to the walls (room size), longest and
shortest reach, where the openings are, and whether it is long and narrow (a hall).

- Breaks ties between rooms that look alike ("this one is smaller").
- Finds doorways: both sides close for a short stretch, then open. A long stretch is a hall.
  Doorways cut the recorded trail into rooms and tell him when to ask.
- Gives each passage the direction of its opening.

It carries little weight alone: the sensors reach about 3.5 m, and furniture and people move.

### Direction

Each place gets its own "house north", fixed at the first look-around.

- **Within a run** the gyro holds direction well.
- **At switch-on and after being carried**, matching the view to a saved look gives direction
  back, because each saved look knows which way it faced.
- **The compass** is recorded with every look from the start, and trusted only after the
  recordings show it reads the same at the same spot on different days. Indoors, and beside his
  own motors, that has to be measured rather than assumed. If it does, it becomes the first hint
  of direction at switch-on and one more tie-breaker.

### Places

- Every room, spot and passage belongs to one place.
- At switch-on he compares a look-around with the spots of every place.
- A clear match: he is there.
- Nothing matches anywhere: "I've never seen this place before. Where am I?" The answer names a
  new place and a new map starts.
- Unsure: he starts a provisional map and joins it to a known place if a later view matches.
- Two free hints. The longer he was switched off, the likelier he was taken somewhere. And the
  network he is on: a router he has never seen plus views he has never seen means a new
  building. (Checked November 22: the router's identity can be read with no extra permission.)
- A person can always just tell him: "you're at my dad's house".

### What the reply model does

- Chooses the destination from the person's words ("go to the kitchen", "take this to Bret's
  room") through the existing drive tool, picking from the list of known rooms.
- Describes each look once during a look-around.
- Asks people where he is, and passes their answer on as a name.
- Talks about the layout: what connects to what, what is in each room.

It does not steer. Routes, turns, distances and "arrived" are worked out in code, on the robot,
with no extra model call per reply.

## Build order

| Slice | What Rex gains | Check before moving on |
| --- | --- | --- |
| **0. Repairs** | A room is entered, not "reached": he goes through the opening. The search turns toward the spoken side or a weak sighting. Automatic adding of views stops. A stale room belief is dropped. | Yesterday's two requests replayed in the simulated base; then on the floor. |
| **1. Tour and spots** | Look-arounds, pictures kept, rooms named by voice, places in the data from day one. | Second walk-through on another day: right / wrong / unsure per room, from the owner's labels. |
| **2. Knowing where he is** | Spot, room and facing from sight plus wheel travel. Replaces the 15-view vote. | Same labelled walk-through; no flicker standing still; wrong and unsure both counted. On the same pictures, today's fingerprint model against one built for places (EigenPlaces), with its speed and memory on the M2. |
| **3. Passages and "go to"** | Trips between rooms joined by a door or hall, with heading nudged by sight. | Ten trips per pair of rooms: arrivals, time, hosted looks. |
| **4. Outline and self-learning** | Room size and doorway finding; everyday trips update the map; he asks when lost. | Look-alike rooms told apart; a new room learned without a tour. |
| **5. Places** | A map per building; "I've never seen this place". | Carried to a second building and back. |
| **6. If needed** | Compass as a hint, once its recordings show it is steady. | Only on measured need. |

Slice 1 keeps pictures so recognition can be scored honestly and a different fingerprint model
can be tried without a new tour. The model changes only if slice 2's comparison shows a clear
gain at an acceptable cost.

## Cost

- **Hosted, once per building:** one description per look, about 80–100 looks at under a cent
  each: roughly $0.50–$1.
- **Hosted, per trip:** none in the normal case; a few looks when lost. Less than today.
- **On the robot:** the fingerprint model already runs. Matching a few hundred saved looks is
  milliseconds. Pictures: about 100 small files per building.
- **Reply size:** the map summary given to the reply model replaces today's room block and must
  stay about the same size.

## Decisions for the owner

1. **House tour:** is a 10–15 minute gamepad tour per building acceptable as the start?
2. **Pictures on disk:** may he keep small pictures of each spot (on the robot only, deleted
   when a room or place is forgotten)?
3. **Map against words:** if someone says "to your right" and the map says the living room is
   to the left, does he follow the map and say so, or follow the words?
4. **Network hint:** may he use the Wi-Fi router's identity to help tell buildings apart?
5. **Going alone:** does he go to other rooms only when asked, or may his own goals take him?

## Not in this plan

A floor plan with every wall, lidar or a depth camera, stairs and other floors, opening doors,
and stickers or markers on door frames (a known fallback if a doorway proves unreliable).
