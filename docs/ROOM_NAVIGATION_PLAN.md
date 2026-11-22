# Room-to-room navigation plan — November 22, 2026

Owner request, after run `8cece04a`: Rex held a book, went through a doorway and down the hall,
then failed "come forward a little, go to your right and go into the living room". Asked for a
room-to-room navigation system with no costly lidar: pictures of each room in every direction,
the on-device room model, sensible links and directions between rooms, range-sensor readings as
an extra layer, maps grouped by building, the layout learned mostly by himself, and the reply
model describing what is in each room.

**Status: plan only. Nothing in this document is built.** Each slice needs the owner's go-ahead.
Revised the same day to follow the [owner's rulings](#owner-rulings-november-22) on the first draft.
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

Web research, November 22. Figures are the sources' own; none was re-measured here. These
systems start from a guided tour. By the owner's ruling Rex does not: every trip he makes is
the teaching run.

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

## Owner rulings, November 22

Given after the first draft of this plan. The design below follows them.

1. **Big rooms divide by direction.** From the middle of the living room, "the kitchen" and "the
   dining room" are directions, not places to stand. The dining room has no floor for him to
   roam. Spots alone miss this.
2. **No gamepad tour.** He learns the layout from what happens anyway.
3. **Voice first.** Unsure how to get somewhere, he asks and follows the directions he is given.
4. **A person's directions outrank the map.**
5. **Photos are kept indefinitely** under `local/`, never committed.
6. **Buildings by router hardware, not network name.** A campus is one network name across
   many buildings.
7. **Only when asked.** For now he never decides by himself to go to another room. It is
   unproven, he would block halls and doors when nobody expects him, and it would alarm the
   owner's partner.

## Design

A **photographic memory with bearings**. Every remembered photo knows where he stood, which way
he faced and how far the floor ran that way. Rooms, the directions between them and the ways he
has travelled are all read from those photos. Research calls the result a topological map. It
is not a floor plan, so it needs no lidar.

### What one remembered photo holds

- **The picture**, small, on his own disk under `local/`, with no expiry.
- **Fingerprint** from the on-device model.
- **Bearing:** which way he faced, in the house's own directions ([Direction](#direction)).
- **Compass reading**, taken with the motors idle.
- **Depth:** the forward depth grid (8×8 distances, looking the same way as the photo) and the
  ring's eight distances at that moment. Together: how far the floor runs that way and what is
  close on each side.
- **Spot:** where he stood.
- **One line** from the hosted picture model on what is there, made once.
- **Which room lies that way**, once a person has said.

A photo is filed whenever he is standing still and has none yet from that spot in that
direction (eight directions, 45° apart, with room for a daylight and a night one).

### Words

- **Place:** a building. Home, Dad's house, work. Each has its own map. (The existing `places`
  tables in the code hold rooms; they become the rooms of the place "home".)
- **Room:** a name people use. A room can be somewhere he stands, or only a direction he can
  face, like the dining room.
- **Open space:** rooms that share one floor with no wall between.
- **Spot:** a point where he has stood still. Spots form by themselves: stopping somewhere new
  makes one.
- **Bearing to a room:** from this spot, that room is that way.
- **Passage:** a way he has actually travelled from one spot to another.

### Rooms by direction

In the open space the photos answer "which way is the kitchen from here?" directly: the photos
from his spot that show the kitchen each carry a bearing.

- **How a bearing gets a room's name.** From a person: "that's the dining room" while he faces
  it, "the kitchen is to your left", or a request he then carries out ("go right, into the
  living room"). The picture model's description may suggest which way to try first, said aloud
  as a question ("Kitchen's that way?"). It never saves a name.
- **"Go to the kitchen" from the living room.** Turn to the kitchen's bearing and roll that way:
  to the spot he has stood on there, or, where he has never stood, as far as the depth readings
  say the floor runs.
- **Rooms he cannot enter.** For the dining room he turns to face it, goes to its edge and says
  that is as far as he fits.
- **Where he is** comes from the spot he stands on, not from what he sees. From the living room
  he sees the kitchen; he is still in the living room.

### Direction

Each place has its own "house north", set by the first photos taken there.

- **Within a run** the gyro holds direction well.
- **At switch-on and after being carried**, matching the view to a saved photo gives direction
  back, because each photo knows which way it faced.
- **The compass** is saved with every photo from the start. A full turn in place is what a
  compass needs to calibrate itself, so every look-around re-fits it at no extra cost. It is
  the first hint of facing at switch-on, choosing which bearing's photos to compare first; the
  photo match decides. Research found indoor compasses up to about 16° off but steady at one
  spot, so each spot learns its own correction. How much it can be trusted beside his motors is
  measured in slice 2, not assumed.

### Knowing where he is

- Compare what he sees with the photos of every spot and bearing in the current place, not with
  15 loose views per room. The best match gives the spot **and** which way he faces.
- Judge a spot against all the others over a short run of looks, not one look against a fixed
  .80 bar. "None of them" is one of the possible answers.
- Add what the wheels say. He can only be at a spot reachable from where he just was, given how
  far he rolled. A hallway cannot become the dining room without travelling there. Standing
  still, the answer cannot flicker.
- Use depth to break ties between look-alike rooms.
- "Lost" is an honest answer. Lost, he looks around; still lost, he asks.
- Being carried (view changes, wheels still) starts the search over, as today.

### Getting somewhere

Only when someone asks ([Staying put](#staying-put)).

1. **He has a route:** he follows the chain of passages. For each one he turns to the direction
   it leaves in, rolls the distance it took before with today's obstacle handling, and aims at
   the opening with the range sensors. Tiny grey pictures kept every quarter metre along the
   passage nudge his heading back onto the line he drove before. The camera never sets speed
   or distance, and a poor match is ignored.
2. **He has only a bearing** (same open space): he goes by direction, as above.
3. **He has neither:** he asks, "Which way is the kitchen?", and follows what he is told, step
   by step, asking again when the directions run out.
4. **Spoken directions always win.** If the map said otherwise, the map is what gets corrected.
5. **Arrived.** By map: the spot he expected is the one he sees, not "something fills the view".
   By directions to a room new to him: he asks once, "Is this it?", and the answer names the spot.
6. **Blocked or lost:** look around, find himself, re-route or ask. A shut door is reported as a
   shut door.

The hosted picture search stays as the fallback for things the map does not hold. Trips by map
need no hosted looks. Yesterday's 4 m hall trip used 11.

### Learning, with no tour

- **Photos** file themselves whenever he stands still.
- **Spots and passages** form from any trip: one he was asked to make, one steered by gamepad
  when that happens anyway, one made by following directions. The trail between two stops
  becomes a passage: direction out, distance, doorway or open floor, direction in, and the
  small grey pictures along it.
- **Names** come from people in the course of talking: "this is the kitchen", a room named in
  a request he carried out, an answer to his own question. A name heard once is a candidate. A
  confirmation, or the same name at the same spot on another day, makes it firm. A correction
  outranks both.
- **A look-around** (one full turn, about 25 s) happens when he is told a room's name, as
  view-collecting does today, and when he is lost. Never as a tour.
- **He remembers what he has asked** and asks once.

Nothing is added to a room from his own belief any more. That is what turned the dining room's
fingerprint into the hallway's.

**What this choice costs.** The map starts empty and fills over days. At first he will ask the
way often. And how often he names the right room cannot be scored until enough named moments
have built up, because those are the only ground truth.

### Depth layer

From the depth saved with each photo, per spot: typical distance to the walls (room size),
longest and shortest reach, where the openings are, and whether it is long and narrow (a hall).

- Tells him how far he can roll toward a bearing.
- Breaks ties between rooms that look alike ("this one is smaller").
- Finds doorways: both sides close for a short stretch, then open. A long stretch is a hall.
  Doorways cut a recorded trail into rooms and tell him when he has entered somewhere.
- Gives each passage the direction of its opening.

It carries little weight alone: the sensors reach about 3.5 m, and furniture and people move.

### Places

- Every room, spot, photo and passage belongs to one place.
- At switch-on he compares what he sees with the photos of every place.
- A clear match: he is there.
- Nothing matches anywhere: "I've never seen this place before. Where am I?" The answer names a
  new place and a new map starts.
- Unsure: he starts a provisional map and joins it to a known place if a later view matches.
- **Router hardware as a hint, never the network name.** Each place keeps the set of hardware
  IDs seen there. Checked on his Mac, November 22:
  - The **router's** hardware address is readable today with no extra permission. It tells
    home from Dad's house from a campus.
  - The **access point's** hardware ID is what tells one campus building from another under
    one network name. macOS hides it from ordinary programs. Reading it needs Location
    permission granted to Rex's program once, in System Settings, by the owner.
- The longer he was switched off, the likelier he was taken somewhere.
- A person can always just tell him: "you're at my dad's house".

### Staying put

- He goes to another room only when asked. His own goals keep him in the room he is in.
- Proposed with slice 4, once he can find doorways: a move he starts himself never ends in a
  doorway or a hall.
- Lifting this is a later owner ruling, after requested trips have proven reliable.

### What the reply model does

- Chooses the destination from the person's words through the existing drive tool.
- Turns spoken directions into drive steps, as it does today.
- Asks the way, asks "Is this it?", and passes people's answers on as names.
- Describes each photo once.
- Talks about the layout: what is which way, what connects to what, what is in each room.

It does not steer. Routes, bearings, distances and "arrived" are worked out in code, on the
robot, with no extra model call per reply.

## Build order

| Slice | What Rex gains | Check before moving on |
| --- | --- | --- |
| **0. Repairs** | A room is entered, not "reached": he goes through the opening. The search turns toward the spoken side or a weak sighting. A stale room belief is dropped. | Yesterday's two requests replayed in the simulated base; then on the floor. |
| **1. Photographic memory** | Photos file themselves with bearing, compass, depth and spot, and stay on disk. Router hardware is noted. Rooms and bearings are named by voice; a look-around follows a naming. Automatic adding to room fingerprints is retired. | Photos of one spot and bearing from two different days match each other. Photo count and disk use reported. |
| **2. Where he is and which way things are** | Spot, room and facing from photos plus wheel travel. In the open space, "the kitchen is that way", and going toward a room by bearing. Replaces the 15-view vote. | What he believed just before each moment a person named or confirmed a room: right, wrong, unsure. No flicker standing still. Today's fingerprint model against one built for places (EigenPlaces) on the saved photos, with speed and memory on the M2. Compass against photo-derived facing, per spot. |
| **3. Asking the way and remembering it** | Follows spoken directions to a room, asks again when they run out, confirms on arrival, saves the trip as a passage, and repeats it later without asking. Requested trips only. | Per pair of rooms: the first trip by directions, then ten by memory. Arrivals, time, questions asked, hosted looks. |
| **4. Depth and doorways** | Room size and doorway finding; look-alike rooms told apart; he knows a hall and a doorway when he is in one. | Look-alike rooms told apart; doorways found on recorded trails. |
| **5. Places** | A map per building from views plus router hardware; "I've never seen this place". | Carried to a second building and back. |
| **Later, by owner ruling** | Going to other rooms for his own reasons. | Not before requested trips are reliable. |

A different fingerprint model is adopted only if slice 2's comparison shows a clear gain at an
acceptable cost.

## Cost

- **Hosted, per building:** one description per kept photo, spread over the days the map fills.
  About 80–100 photos at under a cent each: roughly $0.50–$1.
- **Hosted, per trip:** none by map; a few looks when lost. Less than today.
- **On the robot:** the fingerprint model already runs. Matching a few hundred photos is
  milliseconds. Photos: a few hundred small files per building, tens of megabytes.
- **Reply size:** the map summary given to the reply model replaces today's room block and must
  stay about the same size.

## Still open

- **Location permission on the Mac**, the owner's step, needed only to tell campus buildings
  apart by access point. Home and Dad's house are told apart without it.

## Not in this plan

A gamepad tour, a floor plan with every wall, lidar or a depth camera, stairs and other floors,
opening doors, and stickers or markers on door frames (a known fallback if a doorway proves
unreliable).
