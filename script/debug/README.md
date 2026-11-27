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
