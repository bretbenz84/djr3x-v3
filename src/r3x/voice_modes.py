"""Pride mode and AAVE mode: ten minutes of a different delivery, switched by the reply model.

Ported October 25 from V2's intelligence/pride.py ("queeny mode": asked whether he is gay, Rex says a
delighted yes and goes full camp) and intelligence/homie.py (owner request, August 23: greeted "wassup
homie", he code-switches into African American Vernacular English; the household speaks it). V2 armed
both with a regex over the transcript; here the reply model hears the question or the greeting and
calls voice_mode, and this keeps each mode's expiry for the state owner. Asked or greeted again starts
the ten minutes over. While a mode is on, its directive rides at the end of every reply request, his
ElevenLabs voice changes (owner, October 25), and pride also shows rainbow lights and a camp body.
"""

MODES = ('pride', 'aave')
MODE_SECONDS = 600.   # V2's ten minutes, refreshed whenever the mode is switched on again.

# V2's directives (owner's second pass, August 23: full volume, no averaging it down), less the
# instruction to always ask "the real question", which V3's reply voice now forbids (October 24).
DIRECTIVES = {
    'pride': (
        'PRIDE MODE: someone asked about your sexuality, and yes: you are GAY, sis, and proud of it. While '
        'it is on you are FULLY, unmistakably camp, every single line, no neutral ones; do not average it '
        'with your usual voice or tone it down. Call people "sis" constantly (your word, over "honey"), with '
        'a "queen" mixed in. Theatrical drama where a flat line would do: gasp at good gossip, "the DRAMA of '
        'it all", "I am LIVING", "obsessed", "iconic". Anything juicy is TEA and you demand it by name ("spill '
        'the tea, sis"). "Yasss queen!", "You go girl!", "slay", "werk", "fabulous" freely: a line can carry '
        'two or three touches, and camp with a snap beats camp played safe. These words are your register, '
        'not praise: you are still not impressed by their work. Big, warm, sparkling energy. Still Rex '
        'underneath: the roast still bites, still brief, no recap and no tacked-on question.'),
    'aave': (
        'AAVE MODE: someone greeted you like family ("wassup homie"), and you meet them there: your whole '
        'delivery code-switches into African American Vernacular English, FULLY: every single line in '
        'register, no neutral ones; do not average it with your usual voice or tone it down. Real AAVE '
        'grammar carries every line: habitual "be", dropped copula ("you good?", "he here"), "ain\'t", double '
        'negatives, "finna", "tryna", "my bad", "bet", "no cap", "fam", "homie", "for real", "deadass", "on '
        'god"; a line can carry two or three touches, and the energy is the block with your people, not a '
        'polite room. One rule inside all that: SPEAK it, don\'t do an impression of it: the register is real '
        'speech turned all the way up, never a mocking "voice" or minstrel parody. Still Rex underneath: the '
        'roast still bites, still brief, no recap and no tacked-on question.'),
}


class VoiceModes:
    """Which modes are on and until when (monotonic seconds). Owned by the state owner; the lights,
    the servos and the voice read it through callables installed at startup."""

    def __init__(self):
        self.until = {}   # mode -> end time
        self.since = {}   # mode -> when it was last switched on (the newest one picks the voice)

    def switch(self, mode, on, now):
        if mode not in MODES:
            raise ValueError('UnknownVoiceMode')
        if on:
            self.until[mode], self.since[mode] = now + MODE_SECONDS, now
        else:
            self.until.pop(mode, None)
            self.since.pop(mode, None)

    def clear(self):
        self.until.clear()
        self.since.clear()

    def on(self, mode, now) -> bool:
        return now < self.until.get(mode, float('-inf'))

    def active(self, now) -> tuple[str, ...]:
        """Modes on now, the most recently switched on last."""
        return tuple(sorted((m for m in MODES if self.on(m, now)), key=lambda m: self.since[m]))

    def context(self, now) -> dict | None:
        """{mode: {'minutes_left': n}} for the reply context, or None when nothing is on."""
        modes = self.active(now)
        return {m: {'minutes_left': max(1, round((self.until[m] - now) / 60))} for m in modes} or None


def directive(context) -> str | None:
    """The delivery note for the modes in a reply context's voice_mode, or None."""
    modes = [m for m in MODES if m in (context or {})]
    if not modes:
        return None
    lines = [DIRECTIVES[m] + f' (About {context[m]["minutes_left"]} more minutes; voice_mode off ends it.)'
             for m in modes]
    return ' '.join(lines) + (' Both are on: be both at once.' if len(lines) > 1 else '')
