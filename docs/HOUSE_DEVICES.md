# House devices through Homebridge

Owner request, November 17, 2026: "build an LLM tool-call to control stuff in my house using
Homebridge... I should be able to ask questions about the state of things, or give commands for him to
update things."

**Status, November 17:** built and checked with a fake Homebridge. Against the real Homebridge: Rex's own
non-admin account logs in, reads the thermostat, and switched the Kitchen Light on and off at the owner's
request. Not yet tried in a conversation; the thermostat has never been changed by this code.

## What Rex does

- **Questions.** "Is the porch light on?", "What's the thermostat set to?", "How warm is the house?",
  "What's on?" He reads the device fresh from Homebridge and answers from what it reads, in Fahrenheit.
- **Commands.** "Turn off the kitchen light", "dim the lamp to 30 percent", "make the lights blue",
  "set it to 70", "set the heat to 68", "turn the heat off". He says a few words at once ("On it."), then
  his line about the change only after Homebridge accepted it. If a device refuses or cannot do it, he
  hears why and says so; he never claims a change that did not happen.
- **Several at once.** "All the lights" is every light listed. If some can do it and some cannot (a
  white-only bulb asked to go red), the ones that can change and he says which did not.
- **Only when asked.** The tool is offered on a person's turn only: never in a lull, a goal of his own,
  an arrival greeting, sentry mode, a Jeopardy game, replay or an evaluation.
- **Never opened by voice.** Locks, garage doors, doors, windows, blinds and alarm systems can be checked
  ("is the front door locked?"), never changed. A voice in the room or on the TV must not open the house.
- **No reach, no pretending.** If Homebridge refuses his login, is not answering or lists nothing he can
  use, his context says so and he tells the person plainly instead of acting as if it worked.

Anyone talking to him can use it, as with Alexa. Temperatures are limited only by the thermostat's own
range (40–99 °F for the Honeywell).

## Actions

| Action | Devices | What is written (Homebridge characteristics) |
| --- | --- | --- |
| `check` | any | nothing; one fresh read |
| `turn_on`, `turn_off` | lights, switches, plugs, fans, TVs, purifiers | `On` (or `Active`); `turn_off` on a thermostat is mode off |
| `set_brightness` 1–100 | dimmable lights | `Brightness`, then `On` |
| `set_color` | color or white-tunable lights | `Hue` + `Saturation`, or `ColorTemperature` for whites; then `On` |
| `set_temperature` °F | thermostats | `TargetTemperature` in Celsius at the device's step; the mode stays |
| `heat`, `cool`, `auto` (+ optional °F) | thermostats | `TargetHeatingCoolingState`, then the target if given |

The Honeywell thermostat offers off, heat and cool; it has no auto. Colors: red, orange, yellow, green,
teal, blue, purple, pink, white, warm white and cool white.

## How it works

- `src/r3x/house.py` is the one Homebridge client. At start it makes an HTTP client and a background
  thread that reads the device list every five minutes (about 30 ms), or after 30 s when Homebridge did
  not answer (a refused login still waits five minutes). States are never cached for
  answers: a check reads the device again. Nothing is written at start, refresh or close.
- The reply context carries `house.devices` on people's turns: each name and the changes it takes,
  such as `"THERMOSTAT": "thermostat: turn_off, heat, cool, set_temperature 40-99 F"`. States are left
  out, so he checks before saying what something is doing.
- `house_devices` is one tool with a lead-in (`say_first`) and a spoken result (`text`). For a check,
  `text` is null and a second model call answers from the reading; a change that succeeds needs no
  second call. The state owner runs it outside the memory store; nothing is saved.
- Device names are validated by the state owner, not by the schema: a name that is not listed is
  refused (`UnknownDevice`), nothing is written, and the list is refreshed.
- A thermostat's own temperature-sensor entry repeats its reading and is left out. Repeated names get
  their accessory's name, then a number.
- **Login:** `POST /api/auth/login` with Rex's account; the token lasts eight hours and a 401 logs in
  again once. A token that says admin is refused and dropped (owner: Rex uses his own non-admin account).
- **Limits:** 5 s for a read, 12 s for a write (a thermostat change goes through Honeywell's servers),
  at most six devices written at a time, 2 MB per response.

## Setup

1. In the Homebridge UI (`http://192.168.1.211:8581`), add a user account for Rex with admin rights
   turned off.
2. Put it in `local/secrets.toml`: `homebridge_username` and `homebridge_password`.
3. `local/settings.toml` has `homebridge_url = "http://192.168.1.211:8581"` (set November 17).
   `homebridge_hidden_devices` lists names Rex neither sees nor touches.
4. Check it without changing anything:

   ```sh
   .venv/bin/python -m r3x house
   ```

   It logs in, says whether the account is acceptable, and lists what Rex would be offered with what
   each device reads now.
5. The first time Rex himself reaches the home network, macOS asks whether Python may find devices on
   the local network; until someone allows it, Homebridge looks unreachable. The answer is kept in
   System Settings → Privacy & Security → Local Network for that Python program. This Python is
   built locally (not developer-signed), so a reinstalled or different Python may ask again. Checks run
   from the Claude app use the app's own permission and do not show this.

**November 17 note:** `homebridge-tcc` reports "Outside Temperature" as 53.3 °C (128 °F). It is most
likely Fahrenheit labelled as Celsius. It is in `homebridge_hidden_devices`; Rex has the weather tool
for outdoors.

## Evidence (November 17)

- **Synthetic:** 23 checks against a fake Homebridge (httpx in-memory transport) shaped like the real
  list: the listing, readings, admin refusal, wrong password, Homebridge down, token renewal, every
  action, refusals before any write, partial results, a device refusing a write, cancellation, settings,
  and whole conversation turns through the production session (line spoken only after success, failure
  goes back to the model, check answered from the reading, not offered on his own turns, refused in
  frozen evaluation). The full blocked-I/O suite (1,985) passed in three of four runs; the fourth had one failure that was not captured.
- **Real Homebridge, reads only:** the first saved account was refused as an admin. With Rex's own
  non-admin account, `python -m r3x house` passed: logged in (with the first list read, about 0.5 s),
  offered only the thermostat (no Hue, Kasa or Tuya devices are exposed yet), and left out "Outside
  Temperature" and the thermostat's own temperature entry. Through `House.run`, the tool's `check` read
  the thermostat in about 30 ms (69.1 °F, set to 66 °F, mode off, idle), and an unlisted "Porch Light"
  was refused before any write. The planned writes were computed and not sent: "heat to 68" would write
  heat mode and 20.0 °C; auto, 120 °F and dimming are refused before writing.
- **Real Homebridge, first change (owner's request, November 17 morning):** the Kitchen Light (a
  switch: on and off only) through `House.run`, non-admin account. Dimming it was refused before
  anything was sent; `turn_on` was accepted in 190 ms and read back on; `turn_off` was accepted in
  250 ms and read back off, as it was found. Homebridge's readings matched each time; whether the
  lamp itself lit was for the owner to see.
- **First conversation (November 17, 2:33 PM, run 3a09bdde):** "Hey Rex, can you turn on the kitchen
  light?" Homebridge was unreachable because macOS had not yet let Python onto the home network, so
  the tool was not offered and nothing changed, as designed. But Rex spoke his reasoning before the
  reply ("House devices aren't reachable (Homebridge is not answering), and house_devices isn't among
  this turn's tools, so I'll say so plainly."), echoing the field guide's "say so plainly". Repaired the
  same day: the field guide's house note no longer tells him what to say, Claude's house rule says
  what to tell people when the house is unreachable and that a reply is only what he says aloud, and an
  unanswered Homebridge is retried after 30 s instead of five minutes. Checked with the blocked-I/O
  suite only (house checks and changed areas, 449); the new wording has not been tried on Claude.
- **Not done:** the thermostat has never been changed by this code. Not yet used in a conversation;
  reply latency and model cost not measured.

## Open

- A conversation test with the owner present: a question, the Kitchen Light, the thermostat
  (`set_temperature` and `heat` with a value), something it cannot do, and an unlisted name.
- Hue, Kasa and Tuya devices once they are exposed; check that their names read naturally.
- Fan speed and blinds positions are not actions yet.
- A request whose reply was dropped and is answered later in a lull (an owed answer) gets no house
  tool there, as with weather today; he should say he cannot do it then, not claim it.
