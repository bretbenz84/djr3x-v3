# Reference voices for impressions

`famous/<slug>.wav` + `famous/<slug>.txt` are the reference clips the local Breeze TTS 2
engine clones when the conversation model calls the `impersonate` tool. Each WAV is
24 kHz mono 16-bit, about 17 to 23 seconds (the computer voice 7.5 s), and its `.txt` is
the transcript of exactly that audio: the engine conditions on both, so never pair a
shortened clip with the original full transcript. `index.json` holds display names and
nicknames; the target id the model uses is the slug.

Copied on October 6, 2026 from V2 `assets/voices/famous` at V2 HEAD
`fb96f086925841479952c649b0a39a8f6c77f70c` (regular files only; V2's alias symlinks
`fdr`, `jfk`, `ike`, `lbj`, `teddy-roosevelt` became `index.json` aliases). The fourteen
pre-1993 presidents (Theodore Roosevelt through George H. W. Bush) are public-domain
archive audio prepared by V2's `tools/build_presidential_refs.py` from the Miller
Center, the Internet Archive and the Library of Congress; V2's
`docs/presidential_voice_refs.md` records the spans and licensing basis. The remaining
clips (Obama, Biden, Trump, Harris, Hillary Clinton, Gates, Musk, Huang, Zuckerberg,
Winfrey, Thunberg, the Star Trek computer) were added by the owner to V2 on
August 4, 2026 and are copied as found.

Recordings of real people who talk to Rex are never stored here. A known person's
voice reference is derived at run time from the accepted enrollment recordings in the
core store, under ignored `local/cache/voice_refs/`, and is removed when that person
is forgotten; a stranger's reference comes from the clean samples of their own current
voice track and lasts for the session only. Nothing in `local/` is tracked.
