# Debug tools

Reusable helpers for poking at a running Pi GPUI while developing. Tests live
one level up (`script/windows-smoke.py`, `script/mutation-check.py`).

## Headless by default (Windows)

`PI_GPUI_HEADLESS=1` makes the app open its window on a hidden desktop
(`src/platform.rs`, `enter_headless_desktop`). It paints and answers
automation as usual, but never pops up, never shows in the taskbar and never
takes the focus. The smoke test runs that way unless you pass `--show`.

```
python -I script/debug/app.py start [--fresh] [--show] [folder]
python -I script/debug/app.py do "key ctrl-n" "wait 500" shot
python -I script/debug/app.py status        # process tree (pi under the app) and windows
python -I script/debug/app.py hittest       # what Windows thinks the title bar is
python -I script/debug/app.py ncclick -79   # a real click on maximize (negative x = from the right)
python -I script/debug/app.py dialog C:\some\folder   # answer the native "Select Folder" dialog
python -I script/debug/app.py stop
```

With `start --sandbox-pi` pi gets its own agent folder (`.scratch/headless/agent`), and
`session FOLDER "text"` writes a session there exactly as a terminal pi would (compact JSON:
the app's quick scan looks for `"role":"user"`, so hand-written JSON with spaces is not found).

Look at screenshots with an image viewer or your agent's image reader; `do
shot` with no path writes `.scratch/shots/<time>.png`.

The app runs in a sandbox (`.scratch/headless/home` as `HOME`), so it never
touches your real projects and never takes over launches of your real app.

## A user test with a fake model

`scripted-model.ts` replays steps from a JSON file, so a whole user flow runs
free and offline. A `{"tools": [...]}` step makes several tool calls in one
reply. Example, 50 background subagents at once (about 10 s):

    # steps: [{"tools": [50 x {"tool": "Agent", "args": {..., "run_in_background": true}}]},
    #         {"text": "Found a random fact."}]
    python -I script/debug/app.py start --fresh --scripted steps.json \
        --extension C:\Users\Daniel\source\pi-durable-subagents "P:\some project"
    python -I script/debug/app.py do "wait 2500" "key ctrl-alt-a" "type use 50 subagents" "key enter"

Then read `.scratch/headless/home/.cache/pi-gpui/subagents/*/fleet.json`, the
app log (`.scratch/headless/home/.cache/pi-gpui/logs/pi-gpui.log`) and the
session in `.scratch/headless/agent/sessions/`.

## A real terminal pi, headless

`terminal.py` runs any terminal program in a hidden pseudo console (ConPTY via ctypes, no
packages): type keys, wait for text on its screen, wait for it to exit. With `slow-model.ts`
(a free fake model that answers "SLOW_REPLY_DONE n" after `PI_SLOW_MODEL_MS`) a pi run can be
caught in the middle, e.g. to type `/gpui` while it works. See `gpui_check` and `held_check` in
`script/windows-smoke.py` for complete examples.

```python
sys.path.insert(0, "script/debug")
from terminal import Terminal
with Terminal(["cmd.exe", "/d", "/c", pi, "--offline", "-e", "script/debug/slow-model.ts",
               "--provider", "faux", "--model", "faux-1"], cwd=project, env=env) as term:
    term.wait_for("faux-1"); term.type("hello"); term.type("\r")
```

## Where files go

| Where | What | In git |
|---|---|---|
| `script/debug/` | tools worth keeping, documented here | yes |
| `.scratch/headless/` | the debug app's sandbox (`HOME`, automation folder, `app.log`) | no |
| `.scratch/shots/` | screenshots | no |
| `.scratch/probes/` | one-off scripts for the problem at hand | no |
| `.scratch/archive/<date>-<topic>/` | old probes kept for reference | no |

A one-off that proves useful twice gets cleaned up and moved into `app.py`
(or next to it) with a line in this file.
