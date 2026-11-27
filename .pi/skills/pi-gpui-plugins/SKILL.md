---
name: pi-gpui-plugins
description: Guide to creating Pi GPUI app plugins (buttons, status items, forms and text fields in tool calls, live views, session control) and the pi extensions they ship, with a tested template, recipes, headless testing and troubleshooting. Use when making, changing or debugging a plugin, when the agent should show or ask something in the app's UI, and whenever you learn something new about plugins (then update this skill).
---

# Creating Pi GPUI plugins

A living guide. It grows with every plugin we build: see "Keep this guide
alive" at the end, and follow it in every session that touches plugins.

`docs/PLUGINS.md` (repo root) is the reference for the protocol. This guide is
the practical part: how to build, test and debug, what is not written there,
and what we learned the hard way.

| Read | When |
|---|---|
| this file | always: the model and the steps |
| `references/recipes.md` | building a feature: one recipe per need |
| `references/testing.md` | testing headless, adding a permanent check |
| `references/troubleshooting.md` | something does not load, show or answer |
| `references/platforms.md` | Windows, macOS, Linux differences |
| `references/lessons.md` | before you start (recent lessons), and to record new ones |
| `assets/template/` | the starting point: a tested plugin |

## How a plugin fits in

```
pi (agent) --calls a tool--> Pi GPUI app --tool.start / view.open--> plugin (own process)
                             app draws   <--prompt.form / view.frame / status.set / composer.insert--
pi <--tool result-- the plugin's pi extension (waits for the plugin's answer, e.g. a file)
```

- A plugin is a folder: `plugin.json` plus a program in any language. The app
  starts it and talks JSON lines over stdin/stdout.
- pi never talks to the plugin; the app is in the middle. When the agent must
  take part, the plugin ships a pi extension (`contributes.piExtensions`) that
  the app loads into every pi it starts.
- A plugin can only send the messages in docs/PLUGINS.md. It cannot run code in
  the app, add panels or change the layout. New kinds of UI need new protocol
  messages in the app (Rust): note them in `references/lessons.md` as wishes.

## Build a plugin, step by step

1. **Pick the pieces.** Find your need in `references/recipes.md` (button, text
   into the message box, status, field or form in a tool call, live view,
   sessions, settings).
2. **Copy the template.** `assets/template/` has a button with a shortcut, a pi
   tool, and a field in that tool's call whose answer goes back to pi. Copy the
   folder, then rename: `id` in plugin.json, every command id
   (`<id>.<name>`), the tool name (unique: pi refuses duplicates), the temp
   folder name in both program files.
3. **Write the manifest.** `protocol: 1`, `run` (prefer `["node", "plugin.mjs"]`:
   it starts on every OS), `contributes`, `permissions` only if needed.
4. **Write the program.** Read stdin lines; react to `command.run`,
   `tool.start`, `view.open`, `prompt.result`; send messages as one JSON line
   each. Nothing else on stdout. Exit when stdin closes.
5. **Write the pi tool** (if the agent takes part). It waits for the plugin's
   result on a file watch, returns it, and deletes the file.
6. **Test headless** with a scripted fake model (no tokens, no windows):
   `references/testing.md`. Check the app log and the session file.
7. **Try it in the real app.** Copy the folder into the plugins folder
   (`references/platforms.md`), restart the app, and with a real model ask for
   something that makes it call your tool.
8. **Keep it.** Add a smoke check if the plugin matters, and record what you
   learned (next section).

## Rules worth knowing before you start

- Idle costs nothing: wait on events (stdin, file watches), never poll in a
  loop; send status and frames only when they change; set up heavy things lazily.
- Write handoff files in one step (`.tmp` then rename), sanitize `callId` for
  file names, delete them after reading.
- A prompt or form also closes when its tool call ends: `prompt.result` comes
  with `closed: true` and nobody waits. Ignore it.
- The app reads plugins once at start: restart it after changes. pi extensions
  load when a pi starts: also reload open sessions (Ctrl+Shift+R / ⌘⇧R).
- Log with `{"method":"log","params":{"message":"..."}}`; never print other
  things on stdout.

## Keep this guide alive

This skill must get better every time we work on plugins. In any session that
builds, debugs or reads about plugins:

1. **Before starting**, skim `references/lessons.md`; it holds the newest tips.
2. **When you learn something** (a pitfall, a fix, a faster way to test, a
   protocol detail missing from docs/PLUGINS.md, a platform difference), add a
   dated entry to `references/lessons.md` right away, in the same session.
   Each entry says what, why, and **how it was verified** (a test that ran, a
   screenshot, the code line). Unverified ideas go under "Open questions".
3. **Put lasting knowledge where readers look**: move a lesson into the
   recipe, testing, troubleshooting or platform page it belongs to, and mark
   it "moved to …" in the log. Keep this file short; it is the map.
4. **Fix wrong advice** as soon as it is found: change it, and note the
   correction in the log. Never leave two contradicting statements.
5. **Keep the template working.** After changing `assets/template/`, run
   `python -I script/windows-smoke.py --only template`. When docs/PLUGINS.md
   or the protocol changes, update the recipes in the same commit.
6. **Commit skill changes** with `docs(plugins): ...` so they reach the next session.
