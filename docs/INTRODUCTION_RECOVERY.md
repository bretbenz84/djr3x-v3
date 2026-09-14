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
