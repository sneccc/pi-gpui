# Lessons log

Newest first. Each entry: date, what, and how it was verified. Move lasting
lessons into architecture.md, testing.md, windows.md or pitfalls.md and mark
them "moved to …". Log corrections of wrong advice here too.

## Entries

- **2026-11-27: three smoke groups ran pi with the user's real agent folder**
  (main, handover, lease; also controls' env). Each test pi loaded all of
  Daniel's extensions, started slowly under load (the lease group flaked)
  and wrote durable-subagents databases into his real ~/.pi. Now every group
  uses `sandbox_env()` (empty `PI_CODING_AGENT_DIR`). Verified: 4 full runs
  wrote nothing to ~/.pi/agent/durable-subagents (639 files before and after);
  lease passed 4 of 4. About 232 orphan durable DBs from earlier runs remain
  there (not deleted: some may be Daniel's).
- **2026-11-27: pi.cmd runs two node processes with `--mode rpc`** (the
  launcher, then pi's CLI). A lease names the CLI's pid; compare against the
  session pis present when you read it, not a list taken earlier.

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

- **A first message sent right after a session opens can fail** ("No API key
  found", model "unknown") when pi has not registered extension providers
  yet. Seen with the fake models under heavy load: the template group failed
  1 of 4 full runs, never alone (5 of 5). Does the app need to wait for pi to
  report a model (get_state) before sending, or re-query state? Not checked.

- A message sent about 2 s after a session opened failed with "No API key
  found" and model "unknown": the scripted-model extension had probably not
  registered its provider yet. Seen once in 5 runs (debug app, sandboxed pi).
  Could a user hit it by typing right after opening a session? Does the app
  wait for pi to be ready (get_state with a model) before sending? Not checked.

- Why does "a second click restores the window exactly" still flake under
  load? Idea: the hover highlight fades over time; compare after a `move`
  away and a settle with a longer quiet time, or compare only the sidebar
  region. Not verified.
- Would a raw-pixel `shot` option (no PNG) make the smoke test noticeably
  faster? decode_png costs ~0.9 s per full shot. Not measured end to end.
