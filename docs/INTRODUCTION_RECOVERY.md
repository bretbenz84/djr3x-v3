# Introduction-first identity learning

Implemented September 13, 2026, after the user approved separating a self-reported
name from independently recognized identity.

## Problem and implementation

An introduction previously required a usable acoustic track. Weak similarity to
multiple retained unnamed groups returned no track, rejecting both the name and
subsequent training audio. More speech did not necessarily make progress.

1. The existing conversation model interprets the introduction through
   `introduce_person`. A trusted introduction creates a provisional person record
   even when no usable embedding is available. The reply may use that name without
   claiming acoustic recognition. No regex name inference or extra classifier runs.
2. A usable introduction with ambiguous/weak old-group attribution starts a fresh
   source-bound track after successful execution. It does not relabel the old
   groups or their audio. A clear existing track retains compatible surrounding
   samples. The original turn retains its acoustic attribution alongside explicit
   introduction provenance.
3. Subsequent compatible speech uses the existing bounded enrollment chain and
   quality checks. Three qualifying turns totaling eight voiced seconds still
   establish the independent voice profile. The name is usable before that point.
   Candidates and completed profiles persist across launches.
4. An explicit reintroduction can replace a stalled provisional chain when there
   is no recent competing chain or strong different-track association. Supported
   profiles remain protected against incompatible voices. Sole-face enrollment
   accepts the clean introduction anchor with the existing size, sample-quality,
   conflicting-face and recent-speaker checks. Face absence does not block naming.
5. Tool results distinguish self-report, awaiting usable audio, collecting evidence
   and recognized voice. Learning logs retain the actual acoustic rejection reason
   instead of labeling every weak match as sample quality.

## Limits and verification

No blanket reassignment of weak recordings; no acceptance of overlapping/echo,
untrusted or truncated introductions; no face-to-speaker inference from visibility
alone. A name-only introduction does not authorize attaching arbitrary later
voices. Poor/inconsistent subsequent audio can still fail independent enrollment.
Corrections and forgetting continue through the existing state-owner operations.

379 blocked-I/O checks pass, including seven new regressions covering ambiguous
introduction through the production Session, fresh enrollment with an existing
supported person, restart recognition, stalled-chain replacement, name-only
persistence, cancellation and overlap rejection. Existing wrong-person, correction,
forgetting and face-ambiguity checks also pass. Embeddings and devices are synthetic;
this does not establish live Bret/Jeff discrimination. No live profile, recording,
hardware or hosted service was modified or operated for this implementation.

Next live acceptance: restart Rex, let Jeff introduce himself once and converse
normally, then alternate with Bret. Check both enrollment progress and wrong-name
versus unresolved outcomes, followed by another restart.

## October 20: someone introduced by another person

Owner ruling after run `515d71db`. The owner introduced his partner and told Rex several times
to talk to him; fifteen minutes later nothing of the partner's face or voice was saved. The
third-party introduction above deliberately linked no voice; face learning was tied to one
camera track for 30 s while tracks lasted a median half second, and the model had pointed at the
owner's own blurry face. Now `introduce_person` with subject=other opens a window for the visit
(`NEWCOMER_SECONDS`, renewed whenever the name is said) while the person has no saved voice or face:

- **Voice.** The one unnamed session voice with at least 1.5 voiced seconds on two clips, heard
  from two minutes before the introduction, that is not the introducer's track, was not taken for
  someone by presence or the carry, and whose pooled voice scores under .45 against every saved
  voice (.30 until October 24), becomes the person's hypothesis track and enrollment chain.
  Another such voice heard on two clips links nothing and undoes an existing link (its clips
  stay unnamed evidence); one odd clip, such as a laugh, does not. The voice is saved only by the existing chain: three compatible turns,
  eight voiced seconds, rechecked against every other voice of the visit.
- **Face.** Clear views (the learning-quality test) of the one face that is neither recognized
  nor resembling a saved face (`resembles`: .45 or more against a saved face at any point on that
  camera track) are pooled across track changes. They must look like the face the introduction
  pointed at, if any, even blurry (.45), and like each other (.6), and no saved face may rival
  them; two views spanning 0.8 s are saved. Without a face pointed at, no single view becomes the
  reference, and a lone earlier view that nothing since matches gives way to newer ones.
- **Target.** A face the model names that is recognized as, or resembles, someone else is
  replaced by the one stranger's face in view.
- An objection from the newcomer does not stop gathering (owner); `forget_person` and
  `cancel_association` do.
- `newcomer_voice_waiting` and `newcomer_face_waiting` log once why a voice or face is not
  gathered yet.

Synthetic voices and faces only; not yet run on the robot.

## October 24: the first live run

Run `1bfce8af` opened the window for Jeremy and saved neither. His voice formed its own track
(17 clips, 49 voiced seconds) but sounded .37 like the owner's saved voice (clips .16–.46, as on
October 20), over the .30 ceiling. The owner's clips score .62–.76 against the rest of his voice
and were named at .52–.68 live, so the ceiling is now .45 on two or more pooled clips; his own
off moments can come lower (a tired line formed a track at .30 on October 19), which is why a
voice presence or the carry took for someone is excluded. No face was pointed at, so the face
reference was the first unknown face after the introduction from any view, often the owner's
blurry face on a fresh camera track or Jeremy turned aside, and four minutes of clear views never
matched it. Only clear views count now, and only a face pointed at is a reference. Synthetic
checks; not yet run on the robot.
