# Short-speech capture repair — September 27

Latest-run logs contained four endpoint captures and four transcripts, with no
capture/ASR queue overflow or rejected capture. The final “Shut down.” transcript
requested normal exit. Several earlier non-echo health windows had just one or
three VAD-positive 32 ms frames, with no resulting capture. Other loud windows had
zero VAD-positive frames; amplitude alone does not establish human speech.

The endpoint silently required at least 160 ms of accumulated VAD-positive audio.
That is not utterance length: a short utterance can include many frames below the
VAD threshold. Consequently a VAD trigger could yield no ASR request. Removed this
second duration veto: any VAD-admitted burst now retains the existing preroll and
1.2-second endpoint tail for ASR. No VAD threshold, model, identity requirement or
playback-echo policy changed. The model still determines transcription/trust;
noise sensitivity and false transcriptions require live acceptance.

Capture traces now include VAD-positive duration, and five-second input-health
reports include peak VAD probability. Missing frames and playback echo retain
existing explicit handling. Pure silence still produces no capture.

590 blocked-I/O checks pass using synthetic PCM and scripted ASR/model providers.
Regression covers one through five VAD-positive frames, PCM/preroll/tail retention,
no-speech rejection, and short shutdown reaching ASR while the actual Session
worker is blocked in generation. Shutdown cancels that reply without playing it.
Existing runtime/park tests remain passing. No new hardware or hosted calls made.

Logs establish the short-capture discard path, not the words in discarded audio.
Without retained waveforms, they do not explain zero-VAD bursts or prove all three
reported shutdown attempts were detected. Live short-command recall/false-positive
rates, actual ASR accuracy and load impact remain unmeasured.
