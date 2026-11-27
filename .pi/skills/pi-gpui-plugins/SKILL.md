---
name: pi-gpui-plugins
description: Build, install, debug and test Pi GPUI app plugins (buttons, status items, forms and text fields in tool calls, live views, session control) and the pi extensions they ship. Use when making or changing a plugin, when the agent should show or ask something in the app's UI, or when a plugin does not load or respond.
---

# Pi GPUI plugins

`docs/PLUGINS.md` is the source of truth for the protocol; read the section you
need before writing code. This skill is the short version plus what is not
written there: Windows details, the pitfalls, and how to test headless.

## The model in one picture

```
pi (agent) --calls a tool--> Pi GPUI app --tool.start / view.open--> plugin (own process)
                             app draws   <--prompt.form / view.frame / status.set / composer.insert--
pi <--tool result-- the plugin's pi extension (it waited for the plugin's answer, e.g. via a file)
```

- pi never talks to the plugin. The app sits between them.
- A plugin is a folder with `plugin.json` and any program. JSON lines over
  stdin/stdout. It can only send the messages in docs/PLUGINS.md; it cannot
  run code in the app, add panels or change the layout.
- The agent side is a normal pi extension the plugin ships
  (`contributes.piExtensions`); the app loads it into every pi it starts.

| Need | Use |
|---|---|
| A button or shortcut that does something | `contributes.commands` + `command.run` |
| Text into the message box | `composer.insert` / `composer.setText` |
| A status item in the title bar | `status.set` / `status.clear` |
| Ask the user while a tool runs | pi tool + `toolEvents` + `prompt.input` (one field, `secret` for keys) or `prompt.form` (many questions) |
| Show something live in a tool call | pi tool + `toolViews` + `view.frame` (JPEG/PNG, base64) |
| Drive sessions (open, prompt, follow events) | `"permissions": ["sessions"]` + `sessions.*` |
| User settings | `contributes.settings` (+ `settings.changed`) |
| Links from a tool call to other sessions | `details.piGpui.sessionLinks` or `contributes.sessionLinks` |

## Start from the template

`assets/template/` is a tested plugin with the three most used pieces: a
composer button with a shortcut, a pi tool, and a text field in its call that
returns the answer to the tool. Copy it, rename the id (every command id starts
with `<plugin id>.`) and the tool, then change the behaviour.

Bigger examples in the repo: `examples/plugins/ask` (forms), `secrets`
(masked field, value never reaches pi), `doom` (live view with keyboard).

## Rules that are easy to get wrong

- **Answer handoff:** the plugin gets the user's answer, but the pi tool must
  return it. Write `<tmp>/<plugin>/<callId>.json` in one step (write `.tmp`,
  then rename), let the tool wait on a file watch (plus a slow poll as a safety
  net), and delete the file after reading. Sanitize `callId` for file names.
- **Prompts close by themselves** when their tool call ends: then
  `prompt.result` has `closed: true` and nobody waits. Ignore it.
- **pi refuses two extensions with the same tool name.** Pick unique tool names.
- **Commands show before the plugin runs**; the process starts on first use
  (`activation: "lazy"`). Use `"startup"` only for plugins that serve something.
- **Exit when stdin closes.** With `readline` on stdin, node exits by itself.
- **Cost nothing while idle:** wait on events (stdin, file watches), never poll
  in a loop; send status/frames only on change; start fast, set up heavy things
  lazily.
- **Changes need a restart:** the app reads plugins once at start (no watcher).
  pi extensions load when a pi starts, so also restart open sessions (⌘⇧R /
  Ctrl+Shift+R) after changing `pi-extension.ts`.
- **Logs:** send `{"method":"log","params":{"message":"..."}}` to write into the
  app log. stdout is the protocol: never print anything else there (the app
  logs `plugin X wrote non-JSON`). stderr's last line is shown if it crashes.

## Windows specifics

- Plugins folder: `%HOME%\.config\pi-gpui\plugins\<id>` (`%USERPROFILE%` when
  `HOME` is unset; `XDG_CONFIG_HOME` wins if set). macOS:
  `~/Library/Application Support/pi-gpui/plugins`, Linux: `~/.config/pi-gpui/plugins`.
- `run`: plain names are found on `PATH` with `.exe`/`.cmd`/`.bat`, so
  `["node", "plugin.mjs"]` works everywhere. **`["./run.sh"]` does not start on
  Windows** (the DOOM example has this problem). Prefer node, or a `.cmd`.
- Keybindings: write `cmd-shift-h`; on Windows and Linux it becomes Ctrl+Shift+H.
- Node's home folder is `USERPROFILE`, its temp folder `%TEMP%`.
- Paths in tool results are rendered as Markdown, which eats `\` before
  punctuation (`\.pi` shows as `.pi`). Put paths in backticks or details.

## Test headless (no windows, no tokens)

Tools in `script/debug/` (see its README). The app runs on a hidden desktop in
a sandbox; a scripted fake model makes pi call your tool, so no real model is
needed.

```sh
# steps.json: what the model does, one reply per step
#   [{"tool": "hello_ask_name", "args": {"question": "Name?"}}, {"echo": "GOT: "}]
python -I script/debug/app.py start --fresh --plugin path/to/plugin --scripted steps.json "C:\some\project"
python -I script/debug/app.py do "type hi" "key enter" "wait 1500" shot      # .scratch/shots/<time>.png
python -I script/debug/app.py do "type Daniel" "key enter"                   # answer the field like a user
python -I script/debug/app.py stop
```

- The app log is `.scratch/headless/app.log`: look for `loaded N plugin(s): <id>`,
  `started plugin <id>`, `plugin <id> exited`, `plugin not loaded: <reason>`.
- The `{"echo": "GOT: "}` step makes the model repeat the tool's result, so the
  session file (`.scratch/headless/agent/sessions/**.jsonl`) proves the answer
  reached pi.
- Keys go to whatever has focus: an open field or form takes the keyboard.
- `script/debug/plugin-gallery.py` renders the example plugins into one PNG;
  add a scenario there to picture yours.
- For a permanent check, copy `ask_check` in `script/windows-smoke.py`
  (`wait_for_form` waits until a form is drawn) and add it to `CHECKS`.

## Checklist for a new plugin

1. `plugin.json`: `id`, `protocol: 1`, `run`, `contributes`; ids prefixed.
2. Program: read stdin lines, answer `tool.start` / `command.run` / `prompt.result`.
3. pi extension (if the agent needs it): a tool that waits for the plugin.
4. Install into the sandbox with `app.py start --plugin`, drive it, check the log
   and the session file. Then try it in the real app (copy to the plugins folder,
   restart the app).
5. Clean up: no leftover answer files, the process exits with stdin.
