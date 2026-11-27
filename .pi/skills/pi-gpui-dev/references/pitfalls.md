# Pitfalls (each one cost time)

## Tools and the agent harness

- **Backslashes in bash heredocs are halved** (`\\n` arrives as `\n`). Write
  code with the write/edit tools; in codemode build a backslash with
  `String.fromCharCode(92)`; in Python `chr(92)`.
- **An edit batch fails as a whole** when one `oldText` does not match: nothing
  is applied. Re-read and retry.
- **A copy and an edit of the same file in one parallel batch race.** This lost
  a change from a pushed commit once. Keep them in separate steps.
- **A `read` in the same batch as the command that creates the file** may see
  nothing.
- **sed with backticks or `$(...)` in double quotes** runs them as commands. Use
  the edit tool for text with backticks.
- **Printing pi's screen** (box drawing, spinners) on Windows' default code
  page crashes Python: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
  or `PYTHONIOENCODING=utf-8`.
- **Hand-written pi session JSON must be compact** (`separators=(",", ":")`):
  the app looks for `"role":"user"`.
- **`cargo add`** may bump `Cargo.lock` for unrelated crates; prefer a version
  range that matches what the lock already has, and check `git diff Cargo.lock`.

## App behaviour that surprises tests

- Opening the app on a folder starts a session (one pi) by itself.
- Clicking the empty transcript takes focus from the chat: Ctrl+W stops working.
- The app runs a short `pi --version` (also `node.exe`): find session pis by
  `--mode rpc` in the command line.
- An idle pi exits by itself when its stdin closes: to test the job object,
  kill the app while pi is busy.
- While a tool runs, a spinner animates: "wait until the window is still"
  never finishes.
- A broken file paste falls back to pasting the path as text: check for the
  chip, not just "something changed".
- A fork copies the history (with the same entry ids) into a new session
  file: to tell an edit from a fork, count session files.
- New pi sessions get their file during the first run, at no reliable event:
  the lease polls for the file.

## Repo rules

- **The clipboard is the user's.** Anything that copies (the app's Copy
  buttons, a test probe) replaces what Daniel had. Save it first
  (`Get-Clipboard -Raw`) and put it back (`Set-Clipboard -Value $input`), as
  `subagents_check` does. On 2026-11-27 a probe overwrote his clipboard unsaved.

- Never kill processes you did not start (Daniel's Pi GPUI, his terminal pis).
- Plugins are read once at app start; the bridge is rewritten from the exe at
  start: restart the app (and reload sessions) after changing them.
- Conventional Commits for users; small commits; push to `fork`.
