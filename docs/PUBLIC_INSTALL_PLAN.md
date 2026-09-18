# Public installer and optional hardware

Status: **future design, not implemented**. September 17, 2026 user direction:
eventually publish V3, provide an installer similar in convenience to V2, and let
people select features appropriate to their hardware. This document adds no runtime
settings, scripts, dependencies or services.

## Build on the existing configuration

V3 already reads a strict, non-executable `local/settings.toml`, with documented
fields in `settings.example.toml`. Credentials live in ignored
`local/secrets.toml`, with supported environment overrides. Camera, direction and
object memory already have enable flags. The current profile is dev-mac; the
current implementation does not provide the complete optional-hardware policy below.

Extend this one user configuration surface as each actual adapter is implemented.
Do not add a second `user_config.py`, executable configuration, or a V2-style
chain of overlapping config files. Keep public examples generic and secret-free;
keep local settings, calibrations, credentials, photos, databases and model weights
under ignored `local/`. Settings must have a current consumer, validation and a
clear explanation. This plan does not authorize speculative hardware switches now.

Separate **what the user enabled** from **what is available this run**. Device
detection can help setup suggest choices, but must not silently enable hardware,
open actuator ports merely to identify them, or select a different device when the
configured one is missing. Use stable, unambiguous device identifiers where possible.

## Proposed behavior for optional capabilities

| Condition | Intended behavior |
| --- | --- |
| Feature disabled | Do not load its model, require its optional package, open its device, run workers or expose unavailable action tools. |
| Optional feature enabled and ready | Start explicitly; report the selected device and usable capability. |
| Optional hardware missing or permission denied | Clearly report unavailable status/reason; continue independent capabilities when safe. Do not silently change the user's saved configuration. |
| Required voice input/output unavailable | Explain the problem and do not pretend voice operation succeeded. A text-only mode would need its own supported implementation, not an implicit substitution. |
| Dependency missing | Report the dependent feature as unavailable: no camera means no face/scene/photo evidence; a standard microphone cannot supply ReSpeaker USB direction. |
| Device lost during operation | Stop affected work, cancel pending actions and invalidate stale evidence/targets; independent conversation may continue. Reconnection must not replay old movement commands. |
| Invalid configuration | Reject with a useful explanation rather than silently treating a typo as disabled hardware. |

The LLM must see the capabilities actually available, with explicit unavailable
results for actions that fail after selection. Missing sensors are absence of
evidence, not evidence that nobody or nothing is present. Do not automatically
replace a missing model/backend with a different one.

A simple microphone/speaker installation should be useful without a camera,
ReSpeaker, Arduino LEDs, Maestro servos or a drive base. Camera perception, LEDs,
servos and base motion should be independently selectable where their dependencies
allow. Keep hardware availability separate from choices such as proactive speech,
photo uploads, model/provider selection and recording retention.

## Installer experience — proposed

1. Check the supported OS/architecture, Python and prerequisite tools. Start with
   an explicitly supported Apple Silicon macOS release; current MLX/AVFoundation
   code does not establish Linux, Windows or Intel Mac support.
2. Create/use V3's independent `.venv`; install reproducible dependencies for the
   selected capabilities. Split optional dependency/model groups when implemented,
   rather than installing every perception or hardware package for every user.
3. Ask which hardware/features the user wants. Enumerate safe device information,
   resolve ambiguous choices and write the existing local settings format. Offer
   conservative defaults with physical motion off. Do not assume this owner's
   camera name, calibration, weather location or personal voice configuration.
4. Collect credentials privately and explain hosted text/image use. Show required
   model downloads and storage needs before fetching, verify pinned assets, and
   download only those needed by the selected setup. Do not redistribute personal
   voice samples, memories or restricted assets with the public installer.
5. Run readiness checks and produce a concise enabled/disabled/unavailable summary
   with remediation. Hardware exercise is a separate, explicit test step; successful
   installation must not unexpectedly move the robot or play audio.
6. Offer the supervisor and off-program consoles as an optional installation step,
   following the [operator-tool migration plan](OPERATOR_TOOLS_MIGRATION.md).
   Do not load competing V2/V3 LaunchAgents or bypass hardware ownership handoff.
7. Finish with the correct launch command and config/log locations. Re-running
   setup must preserve credentials, calibration and personal data; configuration
   upgrades need explicit migration/backup, not template overwrite. Uninstalling
   services must not delete the photo library or other personal data.

The first release needs a documented supported-hardware matrix and acceptance
checks for a minimal audio setup, camera setup, this robot's peripherals, disabled
features, missing/unplugged devices and permission failures. Installation should
also be tested on a clean machine/account, not only the development checkout.
Keep public release claims limited to the combinations actually verified.

## V2 reference and scope

Reference: clean V2 `488780557ad70925e3b3c23fb542f544ed51a5b7`.
Read `setup_macos.sh` and `user_config.example.py` for user experience and setup
lessons; consult `setup_assets.py` and `scripts/install_supervisor.sh` for the later
model/service work. Preserve convenience and repeatability, not V2's config loader,
full dependency list, fallback chains or startup structure. This review inspected
source only; it did not run the installer or validate its claimed idempotency.

Implementation follows the immediate core/ownership work. It is documented now
so hardware adapters remain optional and the public installation does not depend
on the original robot's filesystem or equipment.
