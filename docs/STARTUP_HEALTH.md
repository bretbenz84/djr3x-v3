# Spoken startup hardware health

September 25, 2026: live startup reports unavailable configured hardware through
the existing TTS and OutputOwner, without an LLM call or a second audio writer.
Successful device checks are silent. Intentionally disabled capabilities are not
probed or described as missing. Notices are printed and traced even when speech
cannot be delivered; only completed speech enters conversation history.

Camera remains enabled in the local default settings and the prepared
`local/operator-migration/20260925T193611891369Z/robot-settings.toml` launch settings.
A failed camera open produces a spoken camera-unavailable notice and allows voice
operation. Face/scene/object inspection is unavailable for that run: dependent
providers are skipped, not started against a failed camera, and the conversation
receives explicit camera-unavailable context. A subsequent launch retries the
configured camera. There is no hot-plug reconnect worker or configuration fallback.
Camera enumeration precedes loading the face models.

TTS and playback initialize before peripheral devices so required hardware startup
failures can be announced before cleanup closes audio. Servo startup still precedes
microphone capture. Required microphone, servo or LED startup failures still abort;
optional camera/direction failures can continue with a notice. Error wording says
unavailable, since enumeration/open failure does not prove physical disconnection.
No raw provider exception text is spoken. Startup speech has no decorative servo
motion; buffered startup microphone input is discarded before listening begins.

Speech cannot report failures that prevent TTS/playback initialization, nor failures
in preflight or ownership acquisition before provider startup. Those remain console
and diagnostic results. `doctor` is still inert and never plays speech or opens devices.

Verification: 584 blocked-I/O checks pass using synthetic audio and providers.
Coverage includes required failure speech before teardown, optional failure notice,
missing camera and skipped visual dependents, a subsequent connected launch,
startup input clearing, silent healthy startup and unavailable-speech reporting.
Prepared-config doctor reports camera enabled with no missing dependencies/files;
that is not a device-presence test. No hardware, live TTS, hosted model or V2 service
was operated for this change. Audible wording and disconnected/connected camera
acceptance remain pending on the robot.

## November 20: quicker startup, no spoken routine

Owner: the wait for the models and the startup sound effect had become annoying; drop the spoken
"please wait, sound effect, ready" routine and let him start sooner.

**What the logs showed (23 launches, November 19–20).** From the program starting to listening took
26.9 s at the median (24.9 to 31.0). Startup was one queue: lights 0.5 s, the servo wake 8.7 s (nothing
else loaded during it), then wheels, radar and the speech models to 17.2 s, microphone 17.7 s, camera
and faces 19.5 s, everything loaded at 21.6 s, and then 5 s more for the filler to fade, the visor and
the ready line. The logs had no timing for each part, so each local model was timed on its own on the
robot's Mac (real models, no hardware, files already in the disk cache; two runs):

| Model | Job | Load | Memory |
| --- | --- | --- | --- |
| Qwen3-ASR 1.7B 8-bit | speech to text | 3.8–4.2 s | about 2.9 GB |
| Silero VAD | speech present | 0.03 s | 3 MB |
| CAM++ | voice identity | 0.13 s | about 100 MB |
| Smart Turn v3.2 | end of turn | 0.03 s | 20 MB |
| CED-Tiny | voice or noise | 0.06 s | 23 MB |
| Breeze (tokenizer only) | impressions | 0.45 s | 85 MB |
| SCRFD + EdgeFace | faces | 0.15–0.25 s | 44 MB |
| MediaPipe landmarker | smiles | 0.5–0.7 s | 43 MB |
| RF-DETR Nano | pets and objects | 0.25 s | 240–275 MB |
| MobileCLIP-S2 | rooms | 0.25 s | about 410 MB |
| openWakeWord | stop and wake words | 0.18 s | 12 MB |

Together 5.7–6.6 s and about 3.9 GB (live sessions: 3.7 GB at the median after startup, 4.9 GB at most).
The 4.3 GB Breeze model itself loads only for an impression (2.6–3.5 s in six logged loads).

**Built.**
- *Nothing waits its turn unless it has to* (`providers.Alongside`, composed in `App.configure_live`). The
  main sequence is speech output, the whir, lights, the servo wake, the microphone and the direction
  sensor. Beside it: the models and hosted clients start at once and are done before the microphone
  opens; the news, weather, house, music and voice-clone lookups start at once on a thread of their own,
  so a slow network holds up nothing else; once his body is awake the wheels and radar, the camera with
  the scene and current-view providers, and the smile, object and room providers each start beside the
  microphone (since November 21, night, the wake itself runs beside all of these; below). A provider that needs the camera waits for it and is skipped when it is unavailable, as
  before. Startup still finishes only when everything has started, a required failure still stops it and
  is said aloud once, and the closing order is unchanged.
- *The please-wait line and the droid filler are retired.* The whir still plays with the servo wake.
  The ready line was retired with them at first and came back the same day with new lines (below).
  Unavailable hardware is still said aloud.
- *Each start is timed.* A `startup_timing` row in the session log gives, for every part, when its start
  began and how long it took; the same figures are in the startup report's results.

**Still true.** Speech output starts first so a failure can be announced; buffered microphone input from
before listening is discarded. (Until November 21 the servo wake also came before the microphone and the
camera; see "The wake runs beside everything else" below.)

**Not done.** He still does not listen until the camera and wheels have started (they now start beside the
microphone, not after it). Letting him hear before he can see means attaching the camera, wheels and what
depends on them to a conversation already running, which is a change to how the session is put together.
The connections to Claude and ElevenLabs are not opened early: an idle connection is dropped after 5 s, so
one opened at startup is gone before the first reply (a new connection measured about 0.13 s to each).

**Verification.** Synthetic providers only: startup groups (beside the sequence, awaited before a named
provider, required failure, cross-group requirement, interrupt, closing order, timings) and Rex's own
composition with every start replaced by a timed stand-in (models begin during the servo wake and finish
before the microphone; microphone, camera, wheels and radar begin after it). The full blocked-I/O suite
passed (2,126 checks). Real models, no hardware: every local model was loaded on a background thread that
then ended, the speech-side models on one (4.5 s together) and the four camera-side models at the same
time (1.1 s), and each then answered from a different thread. **First robot run (`cdf0ad28`, November 20, started by hand from the coding app and stopped a second
after listening).** The models loaded during the servo wake as intended: all of them by 5.0 s (voice
cloner tokenizer 1.9 s, speech recognition 2.5 s). The whir played without an underrun. The servo start
took 10.9 s (the wake's motion to 8.9 s, then its 2 s settle), the microphone opened at 11.0 s, radar at
11.4 s and wheels at 11.9 s; the visor took 0.9 s. The camera gave no picture in that run: macOS does not
let a program started from the coding app use it (a bare `ffmpeg` from the same shell got no frame
either), so its 10 s wait and the spoken blind notice made that run's 31.7 s to listening
unrepresentative, and what watches through the camera was skipped. Shutdown was clean and no hosted
model was called.

**First wake-word run (`b28d62e8`, November 20, camera working).** Listening began 15.0 s after the
program started and 16.0 s after the wake word was recognized; the four wake-word runs before the change
took 27.2 to 29.3 s from the wake word. Models were loaded by 4.2 s, the servo start ended at 11.2 s, the
microphone opened at 11.3 s, the camera gave its first picture at 13.1 s, wheels were up at 12.3 s, the
smile model (the last to finish) at 14.0 s, and the visor was up at 15.0 s. The whir played without an
underrun. The throttle arm came out at 15.5 s, half a second into listening.

**What that run showed is missing: a ready signal that can be heard.** The owner was beside and behind
Rex (107 degrees left), so no face was in view, Rex greeted nobody, and a lifting visor could not be
seen. Nothing told him Rex was ready; he spoke at about 23 s and Rex's first words came at 28.5 s, later
than the old ready line (about 24 s). To the owner it "didn't seem any faster". The ready line below
is the answer.

**Camera-side models moved into the wake (later the same day, owner's go-ahead).** In that run the
face models loaded inside the camera's start (1.9 s in all) and the smile, object and room models only
after its first picture (0.85 s, the smile model the slowest), while the speech models had been done
since 4.2 s. None of the four needs the camera to load. **Built:** an early step (`providers.Preload`,
`camera_models`) loads them after the speech models, during the servo wake; each model loads once
however often it is started, so the camera, object, room and smile providers find theirs ready, still
own and close them, and still report their own failures. With no camera that run, the models are let go
as soon as startup ends. **Evidence:** synthetic checks (early load, unused models closed, a failing model,
load-once on all four adapters, Rex's start order); real models with no hardware: the four loaded in 1.4 s
on one thread, their owners' starts on four other threads then took 0.00 s each and found the same
models, and each answered from another thread. **Expected** (not yet run on the robot): about a second
sooner, listening near 14 s after the program starts.

**A ready line again (owner, later the same day).** Fifteen short lines the owner picked
(`startup_audio.READY_LINES`: eight of his own, among them "Please state the nature of the medical
emergency.", and seven call-backs to the Star Tours ride). One is said the moment he can listen: the
visor goes up, any hardware notice comes first, then the line with the usual talking motion and mouth
lights, and listening begins when it ends. Lines come round in random order without repeats across
launches (kept by their words in `local/cache/startup-lines.json`, so the retired lists' numbered state
is ignored). The line is chosen and its sound fetched as startup begins, so it plays without a pause
even the first time a line comes up; every line is saved after its first use and the rest are made in
the background after startup. If its sound cannot be had he starts silently and the log says so.
**Evidence:** synthetic checks only. **Expected** (not yet run on the robot): he cannot listen while he
says it, so listening begins about 2 s after the visor, near 16 s after the program starts.

**The owner's startup sound (November 21).** `assets/audio/startup/startup_sequence.wav` replaces V2's whir
(which stays among the copied clips, unplayed): the owner's clip, converted from 48 kHz stereo to the
startup clips' 22,050 Hz mono (the original is kept in ignored `local/audio-originals/`). It is a build
of 11.8 s, a pause of a second, and a finishing sound of 1.4 s. `startup_audio.split_at_pause` drops its
silent ends and cuts it at its longest pause, so another clip of the same shape needs no code change
(a clip with no pause is all build). The build starts with the servo wake, as the whir did, and is heard
about 1.2 s later (the buffered stream). When everything has loaded, `StartupAudio.land` ends the build
(what is queued plays out) and starts the finish; as it begins to sound the lights lock and the visor
goes up, and when it ends (`stop`) the ready line is said. A start that fails or is shut down plays no
finish. The LED board's expected time is 13.2 s to match. **Evidence:** synthetic checks only (the
split, the build and finish through the buffered stream, ready early, no finish on a failed start).
**First real start with it** (wake word, run `0fc38daa`; the owner: "the clip lines up perfectly"):
everything had loaded at 12.9 s, the build ended at 13.6 s (so `land` waited 0.6 s), the finish and the
visor ran to 15.0 s, the ready line from 15.3 to 17.9 s, and listening began at 18.0 s. The camera-side
models loaded during the wake (1.2 s, from 5.3 s) in that run.

**The wake runs beside everything else (November 21, night).** The owner asked for the wake to be a
struggle in step with the sound and lights, then for "one more round" because the first version (run
`9ac3697f`) was over at 9.5 s with the chime at 13.6 s. The wake's motion now lasts 12.8 s
(`servo_poses.WAKE_SECONDS`; the sound's build is heard 1.5 s after the wake begins and lasts 11.8 s, so
the chime is 13.3 s in). In the old order the microphone, camera, wheels and radar waited for the wake and
its 2 s settle, so a wake that long would have moved the ready moment from 13.6 s to about 17 s. Instead
(`App.configure_live`):
- the servo wake is a group of its own beside the main sequence; startup still finishes, and the chime
  still waits, until it is done, and a servo failure still stops startup and is said aloud;
- the microphone opens as soon as the models are loaded (about 5 s in the logged runs). Nothing heard
  before the ready line is kept (`AudioCapture.discard_pending`), and the array arm's hold dates itself
  from when the arm last moved, so the 2 s wait after the wake is removed;
- the camera, and what watches through it, start once the microphone is open (so after the camera-side
  models are loaded). His visor is shut over the camera until the ready moment in either order;
- the wheels and radar start with the wake. Their range sensors are what lets an arm come out while he
  wakes ([servos](SERVO_INTEGRATION.md)); until `RobotServos.awake` the drive owner's back-off reflex is
  off (`DriveBase.reflex_allowed`).

**First robot run with it (`de486648`, wake word; the owner: "it was ok").** Lights 0.17 s; radar and the
wheel board up by 1.4 s; the speech models by 4.6 s and the camera-side models by 5.8 s; the microphone
open at 5.9 s; the camera's first picture at 7.5 s, which was the last start but the wake. The wake's
motion ran 12.77 s, to 12.93 s (planned 12.8 s), the build ended and the chime began at 13.57 s (13.58 s
before the change), the visor was up at 14.4 s, the ready line ran 15.2 to 17.3 s. Both arms were cleared
at 4.47 s. A chime a few tenths late would mean the wake ran long (the build would then end first).
**Evidence:** that run, and synthetic checks (Rex's own composition with timed stand-ins: microphone,
camera, wheels and radar all start and finish while the wake is still running, startup returns only after
it, the camera after the microphone, the reflex off until awake).

**Still to look at:** whether the throttle arm coming out is heard as a sound.
