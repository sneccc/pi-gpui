# Lessons log

Newest first. Each entry: date, what was learned, and how it was verified.
When a lesson has a lasting home in another page, move it there and mark the
entry "moved to <page>". Corrections of wrong advice are logged here too.

## Entries

- **2026-11-27: in a form, typing goes into the own-answer field only after
  Down.** With an option highlighted, Space picks it, so typed text with a
  space in it picks the option instead. Verified: a typed "Maya, she ..."
  came back as the accepted recommendation until the test pressed Down first
  (Grill plugin, `windows-smoke.py --only grill`). Moved to recipes.md.
- **2026-11-27: put a plugin's pure logic in a dependency-free module.** pi's
  `@earendil-works/pi-ai` exists only inside pi, so a test cannot import
  `pi-extension.ts`. The Grill plugin keeps form layout and answer text in
  `format.mjs`, imported by plugin.mjs, the extension and the tests.
  Verified: 5 node tests, 2 node mutations caught.
- **2026-11-27: a plugin can steer a skill without editing it.** Skills
  installed from git are overwritten on update. The Grill tool's
  `promptGuidelines` tell the agent to use `grill_round` whenever it grills
  (grilling, grill-me, grill-with-docs). Not yet verified with a real model:
  the tests use a scripted one.

- **2026-11-27: a skill with ": " in an unquoted description is skipped
  silently.** pi-gpui-dev did not load (no warning) until its description was
  quoted. Verified: `pi --mode rpc --skill <dir>` + `get_commands` listed
  `skill:pi-gpui-dev` only after quoting. Check a skill loads this way after
  editing its frontmatter.
- **2026-11-27: the template is checked automatically.** `windows-smoke.py
  --only template` copies `assets/template`, presses its shortcut, makes pi
  call its tool and answers the field. Verified: 6 checks pass in 6.6 s; with
  the plugin broken on purpose (`prompt.input` renamed) two checks fail.
- **2026-11-27: forms are detected by colour, not by "settle".** While a tool
  runs, its spinner keeps the window changing, so waiting for a still window
  times out (15 s). A form adds accent-coloured (warm, dim orange ~115,85,55)
  borders: count warm pixels before the prompt and wait for +150 (a field) or
  +500 (a form). Verified: Ask check went from 26 s with a failure to 7.6 s,
  6 green full runs. Moved to testing.md.
- **2026-11-27: DOOM does not start on Windows.** Its `run` is `./run.sh`.
  Worked around for pictures with a Python 3.12 venv (`pip install vizdoom
  pillow`) and `run: [venv python, "doom.py"]`. Verified: live frames in
  plugin-gallery.py. Moved to platforms.md. Fix still open (a `.cmd` launcher).
- **2026-11-27: Markdown eats backslashes before punctuation in Windows
  paths.** The Secrets result showed `secrets-home.pi\agent` for
  `secrets-home\.pi\agent`. Verified in a gallery screenshot. Moved to
  troubleshooting.md; an app-side fix is open (todo.md).
- **2026-11-27: Node's home on Windows is USERPROFILE.** Secrets saves under
  `~/.pi/agent/secrets` (`os.homedir()`), which reads `USERPROFILE`, not
  `HOME`. Verified: with `USERPROFILE` set to the sandbox, the saved path in the
  tool result was inside the sandbox (gallery screenshot). Not tested: a run
  with only `HOME` set. Moved to platforms.md and testing.md.
- **2026-11-27: plugins load once, at app start.** No watcher: `loaded N
  plugin(s)` is logged once (src/plugins.rs, after loading the plugin
  directories). Verified by reading the code only. Moved to SKILL.md rules.
- **2026-11-27: first plugin runs on Windows.** Ask, Secrets and DOOM all work
  (forms, masked field, live view with keyboard). Verified:
  `script/debug/plugin-gallery.py`, `windows-smoke.py --only ask`.

## Open questions

- Does a plugin started with `activation: "startup"` restart after a crash, or
  only on next use? (not tested)
- How large can `view.frame` get before the chat stutters on Windows? (DOOM's
  640x400 JPEG is fine; not measured)

## Protocol wishes

- Per-OS `run` (or `.cmd` lookup next to a `.sh`), so one manifest works on
  Windows too.
- Panels or tabs described as data (docs/PLUGINS.md "Planned").
