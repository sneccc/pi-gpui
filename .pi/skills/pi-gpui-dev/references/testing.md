# Testing

Fastest useful check first; the full gate only before committing.

| Check | Command | Time |
|---|---|---|
| One Rust test | `cargo test --release <name>` | seconds after a build |
| All Rust unit tests | `cargo test --release` (123, +1 ignored clipboard test) | ~1 s run |
| TypeScript (lease, /gpui) | `node --no-warnings --test extensions/lease.test.ts packages/pi-gpui/index.test.ts` | ~1 s |
| One smoke group | `python -I script/windows-smoke.py --only edit` | 2-20 s |
| Full smoke (11 groups in parallel, headless) | `python -I script/windows-smoke.py` (`--verbose`, `--serial`, `--show`) | ~23 s |
| TS mutations | `python -I script/mutation-check.py --node` | ~14 s |
| Unit mutations | `python -I script/mutation-check.py [--only TEXT]` | ~5 min all |
| E2E mutations | `python -I script/mutation-check.py --e2e --only TEXT` | ~1 min each (2 release builds) |

Smoke groups: `main`, `controls`, `crash`, `handover`, `lease`, `refresh`,
`gpui`, `held`, `ask`, `template`, `edit`. Each runs its own app in its own
sandbox on the hidden desktop, so they run side by side; each prints one line
with its time. `SMOKE_KEEP=1` keeps a failed run's sandbox.

## The pieces a new smoke check uses (script/windows-smoke.py)

- `Automation(folder).send(...)`: automation commands; `settle()` waits until
  the window is still (not while a spinner runs).
- `fake_pi_agent(sandbox, name, "scripted-model.ts")` + `PI_SCRIPTED_MODEL`:
  pi's model follows a script (`{"text"}`, `{"tool", "args"}`, `{"echo"}`).
  `slow-model.ts` answers after `PI_SLOW_MODEL_MS`.
- `offline_environ()`: the environment without API keys. Always use it.
- `session_pis(app_pid)`: the app's session pis (by `--mode rpc` command line).
- `descendants()`, `process_table()`, `start_time()`, `command_line()`, `alive()`.
- `wait_for(predicate, seconds)`: poll instead of sleeping.
- `wait_for_form` / `chat_accent` / `accent_pixels`: something accent-coloured
  (a form, a chip) appeared. `decode_png` is pure Python (~0.9 s a shot).
- `write_pi_session(agent, project, text)`: a session as a terminal pi writes it.
- `Terminal` (script/debug/terminal.py): a real pi TUI in a hidden console.
- Add the check to `CHECKS`, run it with `--only`, then add a mutation with
  the group name as 5th element and see it caught.

## Writing checks that can fail

- Check the outcome a user would notice, from the outside: processes, files
  (session JSON, leases, state.json), pixels.
- Prefer session-file facts over pixels (`"GOT: ..."` via `{"echo"}`).
- When a check passes on the first try, break the code once and see it fail.
  Three checks passed for the wrong reason before (console window on the
  other desktop, idle pi exiting by itself, paste falling back to text, a fork
  passing an "edit" check).
- Wait for events (a file, a window, an answer), never fixed sleeps; when a
  wait is unavoidable, explain why in a comment.

Automation commands now include `rclick X Y` (Windows): right button, for
context menus.

## Debugging by hand

`script/debug/app.py`: `start [--fresh] [--show] [--sandbox-pi] [--plugin DIR] [--scripted steps.json] [folder]`,
`do "key ..." "type ..." "click X Y" "wait MS" shot`, `status` (process tree,
windows, icon), `hittest`, `ncclick`, `dialog PATH`, `session FOLDER "text"`,
`stop`. Sandbox in `.scratch/headless`, app log `.scratch/headless/app.log`,
pictures in `.scratch/shots/`. `plugin-gallery.py` renders plugins into one PNG.
