# Lessons log

Newest first. Each entry: date, what, and how it was verified. Move lasting
lessons into architecture.md, testing.md, windows.md or pitfalls.md and mark
them "moved to …". Log corrections of wrong advice here too.

## Entries

- **2026-11-27: tool pictures were only visible in an expanded row.** pi's
  `read` returns image content, the app kept it in `Row::Tool.images` but drew
  it inside the collapsed details. Now always drawn under the header.
  Verified: `--only images` counts the picture's colour on screen; the
  mutation "show only after opening the row" is caught.
- **2026-11-27: Node cannot tell a reused pid from a live holder.** The lease
  group flaked: a killed stand-in terminal pi's pid was reused within seconds
  under parallel load, `process.kill(pid, 0)` said alive, and the app's pi
  refused the lease. The app already checks with start times before opening,
  so the bridge now takes the lease over (`holdSession(..., true)`).
  Verified: unit test through the bridge's own `session_start`; 4 green full
  runs; node mutation caught.

- **2026-11-27: a skill with ": " in an unquoted description is skipped
  silently.** pi-gpui-dev did not load (no warning) until its description was
  quoted. Verified: `pi --mode rpc --skill <dir>` + `get_commands` listed
  `skill:pi-gpui-dev` only after quoting. Check a skill loads this way after
  editing its frontmatter.
- **2026-11-27: editing a message in place = tree navigation.** pi has no
  "edit message" RPC. The pencil resolves the row's entry id with
  `get_fork_messages` (like a fork) and sends the bridge's
  `/pi-gpui-navigate <id>`; on a user message pi moves to its parent and the
  app puts the text in the composer. Verified: session file shows the new
  message with the same parent, one session file (`--only edit`).
- **2026-11-27: an edit check passed while the pencil forked.** A fork copies
  entry ids into a new file, so "same parent" was true in both. Fixed by
  counting session files. Verified by the mutation "the pencil forks a new
  session" being caught twice. Moved to pitfalls.md.
- **2026-11-27: message recall is pure logic + a thin view part.** `history_step`
  in text_input.rs decides (unit tested); the view only applies it. The text
  input has no gpui test harness, so testable logic goes into pure functions.
  Verified: 2 unit tests, `--only edit`.
- **2026-11-27: tests must not see API keys.** A test pi whose fake model was
  missing could fall back to a paid provider. `offline_environ()` drops
  keys/tokens; checks assert only `faux/faux-1` answered. Verified: 16
  credentials stripped on this machine; checks pass. Moved to testing.md.
- **2026-11-27: smoke groups run in parallel** (one thread each; the hidden
  desktop is joined per thread). 2m10s became ~23 s. Verified: 6+ green runs.
  Moved to testing.md.
- **2026-11-27: "settle" cannot detect forms** (spinners). Detect accent
  pixels instead. Verified: Ask check 26 s → 8 s. Moved to testing.md.
- **2026-11-27: the leftover flaky check** "a second click restores the window
  exactly" (main group) failed twice in about 15 full runs on 2026-11-27, once
  after the settle fix. Not fixed yet: see Open questions.

## Open questions

- Why does "a second click restores the window exactly" still flake under
  load? Idea: the hover highlight fades over time; compare after a `move`
  away and a settle with a longer quiet time, or compare only the sidebar
  region. Not verified.
- Would a raw-pixel `shot` option (no PNG) make the smoke test noticeably
  faster? decode_png costs ~0.9 s per full shot. Not measured end to end.
