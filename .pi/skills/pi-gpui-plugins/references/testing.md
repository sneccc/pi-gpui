# Testing plugins

Test headless first: the app runs on a hidden desktop in a sandbox (its own
HOME), pi gets its own agent folder, and a scripted fake model makes pi call
your tool. No windows pop up, no tokens are spent, runs take seconds.
The tools live in `script/debug/` (see its README).

## A manual run

```sh
# steps.json: one model reply per step
#   [{"tool": "hello_ask_name", "args": {"question": "Name?"}}, {"echo": "GOT: "}]
python -I script/debug/app.py start --fresh --plugin path/to/plugin --scripted steps.json "C:\some\project"
python -I script/debug/app.py do "type hi" "key enter" "wait 1500" shot     # picture in .scratch/shots/
python -I script/debug/app.py do "type Daniel" "key enter"                  # answer the field like a user
python -I script/debug/app.py stop
```

- `--plugin DIR` copies the plugin into the sandbox's plugins folder
  (repeatable). `--scripted FILE` makes pi's default model the scripted model
  (`script/debug/scripted-model.ts`).
- Step kinds: `{"text": ...}`, `{"tool": name, "args": {...}}`,
  `{"echo": "prefix"}` (repeats the last tool result: proves the answer reached
  pi), optional `"delayMs"`. The last step repeats.
- Automation commands (`do`): `key`, `type`, `click X Y`, `move X Y`,
  `wait MS`, `shot [path] [2x]`. Coordinates are window points as in the shot.
- Where to look: the app log `.scratch/headless/app.log`, the session files
  `.scratch/headless/agent/sessions/**.jsonl`.

## Pictures

`python -I script/debug/plugin-gallery.py [out.png] [--only ask,secrets,doom]`
runs the example plugins side by side and joins captioned shots into one PNG
(about 15 s). Add a scenario to `SCENARIOS` to picture a new plugin.

## A permanent check

Copy `template_check` or `ask_check` from `script/windows-smoke.py`, add it to
`CHECKS`, and run `python -I script/windows-smoke.py --only <name>`. Useful
helpers there: `fake_pi_agent`, `wait_for_form` (a form or field is drawn),
`chat_accent`, `Automation.settle`, `write_pi_session`. Prove the check can fail:
break the plugin on purpose once and see it fail (then restore it).

## Things that bite when testing

- Keys go to whatever has focus. An open field or form takes the keyboard;
  before it appears, keys land in the message box.
- Do not wait for the window to "settle" while a tool runs: its spinner keeps
  changing the picture. Wait for the tool call in the session file, then for
  the form (`wait_for_form`).
- The template's shortcut is `cmd-shift-h` in the manifest; automation sends
  `ctrl-shift-h` on Windows.
- Node takes its home folder from `USERPROFILE` on Windows: set it to the
  sandbox when a plugin or extension writes under `~` (Secrets does).
