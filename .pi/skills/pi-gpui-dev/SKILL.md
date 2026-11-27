---
name: pi-gpui-dev
description: "Working on the Pi GPUI repo (Rust + GPUI app, its pi bridge extension, the pi-gpui pi package, Windows port): where things are, how to build, test headless and debug fast, commit rules, and the pitfalls we already paid for. Use for any change, bug or test in this repository, and whenever you learn something new about it (then update this skill)."
---

# Working on Pi GPUI

A living guide for this repo. Read it before changing code here, and keep it
current (last section). Plugins have their own guide: `pi-gpui-plugins`.

| Read | When |
|---|---|
| this file | always: the map, the loop, the rules |
| `references/architecture.md` | finding where something lives, how app and pi talk |
| `references/testing.md` | choosing and running the right check, writing a new one |
| `references/windows.md` | anything Windows: gpui quirks, processes, paths, headless |
| `references/pitfalls.md` | before a session, and when something behaves oddly |
| `references/lessons.md` | the newest learnings, and where to record yours |

Project docs worth knowing: `docs/APP_PRINCIPLES.md` (how the app talks to
pi), `docs/GPUI_STYLE_GUIDE.md` (how views are built), `docs/PLUGINS.md`,
`docs/WINDOWS_UI_CHECKS.md`, `CONTRIBUTING.md`, and `todo.md` (session notes,
untracked: the current plan and state of the Windows branch).

## The repo in one picture

- `src/` the app (Rust, gpui 0.2.2). `hub.rs` = window, sidebar, tabs;
  `chat.rs` = one session (transcript, composer, pi process); `pi.rs` =
  finding and starting pi; `sessions.rs` = reading pi's session files and
  leases; `plugins.rs` = app plugins; `platform.rs` / `process.rs` =
  OS-specific pieces.
- `extensions/pi-gpui-bridge.ts`: a pi extension compiled into the exe
  (`include_str!`) and loaded into the app's own pi processes.
- `packages/pi-gpui/`: the `/gpui` pi package for terminal pi users.
- `script/`: smoke test, mutation check, debug tools (`script/debug/`).
- The app never contains the agent: it runs `pi --mode rpc` per session and
  talks JSON lines. See `references/architecture.md`.

## The work loop

1. **Understand first.** Read the code around the change; check
   `references/pitfalls.md` for the area.
2. **Smallest fast check first**: one unit test (`cargo test --release name`),
   one smoke group (`python -I script/windows-smoke.py --only edit`), one
   mutation (`--only TEXT`). Full runs only before committing.
3. **Debug headless**: `python -I script/debug/app.py start --fresh [--scripted steps.json] [folder]`,
   then `do "..." shot`, look at the picture, `stop`. No windows pop up.
4. **Prove tests can fail**: break the change on purpose once (a mutation in
   `script/mutation-check.py`), see the check fail, restore.
5. **Gate before commit** (all green):
   `cargo fmt && cargo clippy --all-targets && cargo test --release`,
   `node --no-warnings --test extensions/lease.test.ts packages/pi-gpui/index.test.ts`,
   `python -I script/windows-smoke.py` (about 23 s).
6. **Commit small** with Conventional Commits written for users (AGENTS.md),
   one change per commit, then `git push fork windows-support`. Rerun the
   quick tests on what you committed when you split a file across commits.

## Rules that save hours

- Release builds can't replace a running exe: move it aside first
  (`move_running_exe_aside()` in `script/mutation-check.py`); never kill
  Daniel's own Pi GPUI.
- Edit files with the edit/write tools. Never generate code through bash
  heredocs containing backslashes (they get halved).
- Tests and tools run headless, sandboxed (own `HOME`, own
  `PI_CODING_AGENT_DIR`), without API keys, and clean up after themselves.
- A pi with the fake models (`script/debug/slow-model.ts`,
  `scripted-model.ts`) costs nothing; never let a test reach a real model.
- Ask Daniel before anything outward-facing: releases, PRs, new repos, npm.

## Keep this guide alive

This skill must get better each time we work here. In every session:

1. **Before starting**, skim `references/lessons.md` (newest first).
2. **When you learn something** (a pitfall, a fix, a faster check, how a part
   of the app works), add a dated entry to `references/lessons.md` in the same
   session, with **how it was verified** (test, screenshot, code line).
   Guesses go under "Open questions".
3. **Move lasting knowledge** into the reference page it belongs to and mark
   the log entry "moved to …". Keep this file a short map.
4. **Fix wrong advice** as soon as it is found and log the correction; never
   leave two statements that disagree.
5. **When a tool, command or file moves**, update the pages that name it.
6. **Commit skill changes** with `docs: ...` so the next session has them.
   After editing frontmatter, check the skill still loads:
   `printf '{"id":"1","type":"get_commands"}\n' | pi --mode rpc --offline --skill .pi/skills/<name>`
   must list `skill:<name>` (a bad description is skipped silently).
