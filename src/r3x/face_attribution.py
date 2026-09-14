"""State-owner policy for semantic face targets and persistent face learning."""
from .faces import rank_face
from .speakers import cosine


def public_face(row):
    return {key:value for key,value in row.items() if not key.startswith('_')}


def target_allowed(face, faces, subject, attribution, pending, recent_competitor=False,
                   introduction_source=False):
    if subject == 'self':
        # A short, clean introduction can identify the sole visible face without
        # supplying enough audio to train a voice profile. Passive learning keeps
        # its existing acoustic eligibility gate.
        source_allowed = attribution.learning_eligible or (
            introduction_source and attribution.track_id is not None and
            attribution.reason in {'session_track', 'acoustic_match', 'self_introduction'})
        return bool(face.get('sole_face_throughout') and len(faces)==1 and
            face.get('box',[0,0,0,0])[2]*face.get('box',[0,0,0,0])[3]>=.025 and
            source_allowed and not recent_competitor)
    # A targeted delivered question or a uniquely presented face permits an introducer to answer.
    if pending and pending.get('face_track_id')==face['track_id']: return True
    if len(faces)==1: return True
    presented = [f for f in faces if f.get('newly_presented')]
    if len(presented)==1 and presented[0]['track_id']==face['track_id']: return True
    unknown = [f for f in faces if not f.get('identity')]
    known_source = attribution.person_id and any(f.get('identity')==attribution.person_id for f in faces)
    return bool(known_source and len(unknown)==1 and unknown[0]['track_id']==face['track_id'])


def learning_samples(face, capture, person_id, store):
    samples = [s for s in face.get('_samples',()) if s.eligible and s.vector is not None and
               s.session_id==capture.session_id and capture.started_at<=s.at<=capture.speech_ended_at]
    if len(samples)<2 or samples[-1].at-samples[0].at<.8: return (), 'need_clear_compatible_views'
    namespace = samples[0].namespace
    if any(s.namespace!=namespace for s in samples): return (), 'namespace_conflict'
    if any(cosine(a.vector,b.vector)<.6 for a in samples for b in samples): return (), 'incompatible_face_views'
    profiles = store.face_profiles(namespace)
    own = profiles.get(person_id)
    for sample in samples:
        ranked = rank_face(sample.vector, profiles)
        own_score = sum(cosine(sample.vector,v) for v in own)/len(own) if own else None
        rivals = [(p,score) for p,score in ranked if p!=person_id]
        if rivals and rivals[0][1]>=.45 and (own_score is None or own_score-rivals[0][1]<.1):
            return (), 'competing_face_identity'
        if own and own_score<.55: return (), 'existing_face_profile_conflict'
    return tuple(samples), 'eligible'
