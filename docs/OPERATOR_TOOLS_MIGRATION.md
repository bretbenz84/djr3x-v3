# Supervisor and off-program controls: V3 migration

Status: **documented requirement only; not implemented or deployed.**
User direction, September 17, 2026: preserve the useful V2 Rex supervisor and
servo-control program in V3, ensure clean hardware handoff, and migrate the macOS
LaunchAgents. No code, installed agents, processes or hardware were changed for
this documentation task.

This is a prerequisite for physical LED/servo/base integration, not optional
cleanup after the drivers are connected. The operator must retain convenient
physical control while Rex is off. The eventual public installer should offer this
service setup as an optional step; see [public installation and hardware choices](PUBLIC_INSTALL_PLAN.md).

## Required behavior

- Make independent V3-owned copies/adaptations of the supervisor and servo console.
  Use V3's `.venv`, configuration, runner and ignored `local/` logs/state. Do not
  import V2 code or depend on its `.env`, environment, mutable data or startup chain.
- Preserve wake/start behavior and the servo console's useful controls, including
  position feedback, calibrated channel limits and the existing reset/home action.
  Inventory these behaviors against active V2 code before copying; do not assume
  old documentation reflects the current launcher.
- The supervisor owns its wake-listening microphone only while the controller is
  off. It releases audio resources before V3 starts and resumes after confirmed
  shutdown. It must recognize manually started controllers as well as its own child.
- The servo console releases the Maestro connection before V3 opens it, disables
  manual writes while Rex owns it, and resumes only after V3 has closed it. Discard
  stale queued slider commands so they cannot execute when control returns.
- Use a shared controller/ownership contract across the supervisor, console and
  V3 runner. Preserve single-instance behavior throughout startup, runtime and
  shutdown; recover after failed startup or a crash. Account for a manually started
  V2 controller during the transition so two versions cannot claim the same robot.
- Controller ownership notification is not proof that a port is already released.
  Wait for confirmed release with a bounded handoff and clear diagnostics. A timeout
  must not result in two writers or forced takeover of a live controller's hardware.
  Avoid opening ports merely to probe ownership: doing so can reset Arduino boards.
- Inventory the related battery/base and LED menu-bar tools when planning the
  migration. If installed, they share this hardware-ownership problem and must not
  be left contending with V3. Preserve useful off-program operation; sequence their
  adaptations with the corresponding V3 hardware support.

## LaunchAgent migration plan — future execution

1. Inspect the actually installed plist paths, labels, running jobs and child
   processes. Source templates are references, not proof of what is installed.
2. Record/back up the existing definitions and settings for rollback. Preserve V2
   as a working baseline; do not edit its checkout to make V3 work.
3. Prepare V3 launch definitions with its absolute interpreter, working directory,
   executable/arguments, device configuration and log paths. Verify the V3 runner's
   shutdown interface rather than assuming V2 `main.py` behavior exists.
4. At the planned cutover, unload/disable the old relevant LaunchAgents and verify
   their processes and device handles are released before loading V3 replacements.
   Prevent KeepAlive/login restart from bringing the old owner back alongside V3.
5. Verify startup at login, voice launch, manual launch, normal shutdown, failed
   startup, crash recovery and return of off-program controls. Confirm only one
   supervisor/controller set is active. Keep a documented rollback to the old
   agents, restoring them only after the V3 owners have exited and released devices.

V2's supervisor also includes automatic Git updating and recovery behavior. Review
these separately before adaptation; do not silently introduce automatic checkout
changes or copy policies that assume V2's paths, exit codes or environment.

## Acceptance evidence required when implemented

Synthetic checks must cover ownership races, bounded timeout, failed launch,
cancellation, stale command rejection and crash recovery without hardware access.
Explicit live checks must verify microphone handoff, serial release/reacquisition,
manual and supervised V3 launch, off-program servo controls, and LaunchAgent
cutover/rollback. Add LED/base companion checks if those services are included.
Do not claim migration complete from a plist edit or passing synthetic tests alone.

## V2 reference, inspected read-only

Clean V2 revision `488780557ad70925e3b3c23fb542f544ed51a5b7` on September 17:

- `rex_supervisor.py`, `tools/rex_servo_menubar.py`
- `utils/single_instance.py`, `utils/port_handoff.py`
- `scripts/install_supervisor.sh`, its referenced `launchd/` templates
- `docs/supervisor.md`; companion references `tools/rex_battery_menubar.py` and
  `tools/rex_led_menubar.py`

The active launcher starts headless `main.py`; the supervisor document still has
older `main.py --gui` wording. The supervisor itself does not hold serial ports:
menu-bar tools observe the controller lock and release them. V2's handoff helper
can continue after a timeout; that behavior is not the V3 ownership requirement.
Installed jobs and live handoff behavior were not inspected or validated here.
Record extracted dependencies, source digests and implementation checks when the
future adaptation is actually performed.
