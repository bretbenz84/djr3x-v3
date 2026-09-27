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
