# UI checks on Windows

The app can be driven from a script: it takes keys and clicks and saves pictures of its own window. The real pointer and keyboard stay free and the window can sit behind other windows. The code is `src/automation.rs` (shared) and `src/automation/windows.rs`. Unlike macOS it is in release builds too, but it only turns on when `PI_GPUI_AUTOMATION` is set. The commands are the same as on macOS, see [MAC_UI_CHECKS.md](MAC_UI_CHECKS.md).

## Start

```bat
set PI_GPUI_AUTOMATION=C:\tmp\ui-auto
set PI_EXECUTABLE=%USERPROFILE%\.pi\agent\bin\pi.cmd
target\release\pi-gpui.exe C:\path\to\project
```

With automation on the app does not bring itself forward. Set `HOME` to an empty folder first if the test should not see your projects and settings (the app keeps them in `%HOME%\.config\pi-gpui`, `HOME` falls back to `USERPROFILE`).

## Send commands

```bat
python -I script\ui-command.py C:\tmp\ui-auto "click 100 232" "type hello" "wait 300" "shot C:\tmp\a.png"
```

The script writes the lines to `command`, waits for `done` and prints one result per line (`ok` or `error: ...`). Coordinates are in points from the window's top left corner, as in the screenshot.

## How it differs from macOS

- Mouse: `WM_MOUSEMOVE` / `WM_LBUTTONDOWN` / `WM_LBUTTONUP` are posted to the window's own message queue. gpui handles them like real input. Only the left button; no drag, wheel or modifiers.
- Keys and `type` go through gpui, as on macOS.
- `shot`: `PrintWindow` of the client area (no frame or shadow), 1 px per point. With `2x` after the path you get device pixels (the same size at 100 % scaling).
- A minimized window cannot be captured.
- Native dialogs (the folder picker behind `key ctrl-o`) are separate windows: `shot` shows only the app window, and clicks and keys sent here do not reach the dialog. Drive it from outside, for example by setting the text of its edit control and clicking OK with `SendMessage` / `BM_CLICK`.

## Tests

Three layers, cheapest first:

| What | Command | Needs |
|---|---|---|
| Unit tests: path encoding, links, drive-letter image paths, process lookup, Recycle Bin, and the automation's pixel and mouse maths | `cargo test` | nothing |
| End-to-end smoke test: starts the real exe in a sandbox and drives it through the protocol above | `python -I script/windows-smoke.py` | a release build, `pi` on PATH |
| Mutation check: puts one bug at a time into the source and requires the tests to fail | `python -I script/mutation-check.py` (unit), `--e2e` (smoke test) | a clean working tree |

The smoke test sends no prompt, so it costs no tokens. It checks that pi is found without `PI_EXECUTABLE`, that bad commands report errors, that a screenshot has real content, that a click changes the window, that the saved project path has no `\?\` prefix, that no console window appears, and that closing a session or quitting leaves no pi process behind.

Notes from writing them:

- Opening a folder starts a session of its own, so the app has one pi before you press Ctrl+N. The smoke test tracks process ids per session because of this.
- Clicking the empty transcript takes the focus off the chat, and then Ctrl+W no longer reaches the session. Click a button, not the page, between steps.
- The focus check is best effort: Windows' foreground lock stops a background-launched app from taking the focus even when it asks to, so a regression there cannot be caught.
