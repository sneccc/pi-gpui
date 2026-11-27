# TODO: Pi GPUI on Windows (our fork)

Written 2026-11-26 at the end of a long session run from `P:\Projects\svg_experiments`.
Continue in a **new pi session started in this folder** (`P:\Projects\pi-gpui`).
This file is untracked on purpose (session notes, not project docs). Keep it updated while working; delete it when done.

Workflow: `cargo fmt`, `cargo clippy --all-targets`, `cargo test` and `python -I script/windows-smoke.py` before every
commit; small commits on `windows-support`, push to `fork`. Ask Daniel before anything outward-facing (new repos, releases, PRs).

## What this is

- Upstream: <https://github.com/bytesbay/pi-gpui> (`origin`). A native GUI for the pi coding agent, Rust + GPUI.
  Upstream supports macOS and Linux only ("Windows isn't supported" in its README).
- Our fork: <https://github.com/sneccc/pi-gpui> (`fork`, public). Branch **`windows-support`** carries the port.
  The fork's `main` is still plain upstream. **Open question for Daniel:** fast-forward the fork's `main` to
  `windows-support`, or keep `main` tracking upstream? (Asked, not answered yet.)
- Windows 11, x86_64-pc-windows-msvc, Rust 1.90.0 (pinned by `rust-toolchain.toml`), gpui `=0.2.2`.
- pi on this machine: 1.6.1 at `C:\Users\Daniel\.pi\agent\bin\pi.cmd` (an npm-style `.cmd` shim around node).

## State of the branch

Everything is pushed to `fork/windows-support` (last push: `097ab76`). Working tree clean except this file.

```
097ab76 test: run the Windows smoke test headless on a hidden desktop
6ab850c feat: hand a second launch over to the running app on Windows
6470f48 Windows: paste files copied in Explorer
4eb07ca Windows: stop pi with the app, and stop its whole process tree
b1cabdb Windows: draw minimize, maximize and close buttons
88ecec6 Add Windows end-to-end smoke test and mutation check
8ccda28 Add Windows support
44b33db chore: release 0.2.11                                     <- upstream
```

New commits follow Conventional Commits (AGENTS.md); the older `Windows: ...` subjects predate that.
Push with `git push fork windows-support` after each green commit.

## How to build, test, run

```
cargo build --release
cargo test                      # 113 unit tests (+1 ignored: clipboard, overwrites the clipboard: cargo test -- --ignored clipboard)
cargo fmt && cargo clippy --all-targets
python -I script/windows-smoke.py [--only main,controls,crash,handover,lease,refresh,gpui,held] [--serial] [--verbose] [--show]
                                                  # end-to-end, HEADLESS, 8 groups in parallel: ~22 s (was 2m10s)
node --no-warnings --test extensions/lease.test.ts packages/pi-gpui/index.test.ts       # TS: lease + /gpui helpers, ~1 s
python -I script/mutation-check.py --node        # TS mutations, ~8 s
python -I script/mutation-check.py                # 13+ unit-level mutations, ~5 min
python -I script/mutation-check.py --e2e [--only TEXT]   # end-to-end mutations (rebuild + smoke each, ~80 s each)
python -I script/debug/app.py start|do|status|hittest|ncclick|dialog|stop   # headless debugging, see script/debug/README.md
run.bat [folder]                                  # start the app on screen (only when Daniel wants to see it)
```

- **Prefer headless for testing and debugging** (Daniel does not want windows popping up): `PI_GPUI_HEADLESS=1` puts the
  window on the hidden desktop `pi-gpui-headless`; it paints, `shot` works, it never steals focus. `script/debug/app.py`
  wraps it with a sandbox HOME in `.scratch/headless`. Window-level tools must `SetThreadDesktop` onto that desktop first.
- **Where scripts go:** reusable debug tools in `script/debug/` (documented in its README, committed); one-offs in
  `.scratch/probes/`, screenshots in `.scratch/shots/` (`.scratch/` is gitignored). The old session helpers are in
  `.scratch/archive/2026-11-26-windows-port/{probes,patches,shots}`. Do not scatter files in `P:\Projects\tmp` any more.
- **Stop the app before building** (`python -I script/debug/app.py stop`, or `taskkill /F /IM pi-gpui.exe`), or cargo
  cannot replace the exe. `mutation-check.py --e2e` no longer kills anything: it renames a running exe aside. Give an e2e mutation a 5th
  element (a `windows-smoke.py --only` name) so it runs one smoke check instead of all.
- ConPTY child of a redirected parent: set STARTF_USESTDHANDLES with empty handles, or the child writes to the
  parent's pipes and pi exits at once (no terminal). A fast program's last output arrives just after it exits: `settle()`.
- Renaming a running exe always works, so "can I rename it" says nothing about whether it runs: always move it aside.
- Smoke speed (2026-11-27): groups run in threads (each joins the hidden desktop itself; desktops are per thread).
  Fixed sleeps replaced by waiting for the event: `hit_test` uses SendMessage (39 s -> 0.02 s), ctrl-q no longer waits
  30 s for an answer the quitting app cannot give, `Automation.settle` waits for the window to stop changing. Session
  pis are found by command line (`--mode rpc`, `command_line()` via NtQueryInformationProcess) because the app's own
  short `pi --version` is also a node.exe. Remaining cost: PNG decoding in pure Python (~0.9 s per full shot);
  the fix would be a raw-pixel option in the app's `shot`.
- Three e2e mutations had silently survived (console window, job object, clipboard paste); now caught: the stray-window
  check also looks at the normal desktop, the crash check kills the app while pi is busy (slow fake model as default
  via an agent settings.json), and the paste check looks for the chip's accent colour.
- Rust tests: use `crate::test_support::TempDir` for temp folders (removed even when the test fails). Upstream tests
  still use bare `temp_dir()` joins (chat/usage, selectable, session_links, plugins, session_search, user_theme,
  attachments): convert them when touching those files.
- **Never put a backup (cp) and an edit of the same file in one parallel tool batch**: they race. On 2026-11-27 this
  lost the /gpui message from commit 9faff18 (fixed in c845d35). After splitting a file across commits, rerun the
  quick tests (`node --test ...`, `windows-smoke.py --only ...`) on the committed result before pushing.
- **Never write patch scripts as bash heredocs containing backslashes** (they are halved: `\\n` arrives as `\n`). Use the
  edit tool or codemode (build backslashes with `String.fromCharCode(92)`). This bit twice on 2026-11-27.
- Hand-made pi session files must be compact JSON (`separators=(",", ":")`): the app's scan looks for `"role":"user"`.
  `script/debug/app.py session` and `windows-smoke.py write_pi_session` do it right.
- Run one heavy job at a time.
- UI automation: `PI_GPUI_AUTOMATION=<folder>` then `python -I script/ui-command.py <folder> "click 100 200" "shot C:\x.png"`.
  Commands: `key`, `type`, `click`, `move`, `wait`, `shot [2x]`. See `docs/WINDOWS_UI_CHECKS.md`.
  Use the app's own `shot` for screenshots (Daniel asked for API-only screenshots).
- Sandbox a test run with `HOME=<empty folder>`; the app keeps its state in `%HOME%\.config\pi-gpui`.
- Project rules (`CONTRIBUTING.md`): `cargo fmt`, `cargo test`, `cargo clippy --all-targets` before pushing.

## What is done (all on the branch)

- Builds and runs on Windows; no console window for the app (`windows_subsystem`) or for children (`process::command`
  uses `CREATE_NO_WINDOW`; without it the main window got minimized).
- Paths: `\\?\` prefix stripped (`sessions::canonical_path`, `strip_verbatim`), `HOME` falls back to `USERPROFILE`.
- pi lookup: `PATH` search tries `.exe/.cmd/.bat/.com`; no login shell on Windows. Recycle Bin (`SHFileOperationW`),
  Explorer reveal/open, process liveness (`OpenProcess`), drive-letter image paths in messages.
- UI automation on Windows (`src/automation/windows.rs`): mouse as `WM_MOUSE*` posted to the window, shots via `PrintWindow`.
- Window buttons: minimize / maximize / close drawn as window control areas, plus double-click on the caption.
- `process::contain_children` (job object, kill on close) so pi dies with the app even if it is force-killed;
  `process::kill_tree` (`taskkill /T`) because `Child::kill` only killed the `cmd.exe` wrapper of `pi.cmd`;
  `process::spawn_outliving` for editor/Explorer (breaks away from the job).
- Paste files copied in Explorer (`CF_HDROP`).
- Single instance on Windows: a second launch hands its folder / `pi-gpui://` link to the running app over a named pipe
  (`src/single_instance.rs`) and exits; the running window is restored from the taskbar (`platform::restore_if_minimized`).
- Headless mode (`PI_GPUI_HEADLESS=1`, `platform::enter_headless_desktop`) and `script/debug/app.py`.
- Tests: unit tests for all the above, `script/windows-smoke.py` (end-to-end, headless, 3 green runs in a row on
  2026-11-26), `script/mutation-check.py` (13 unit + several e2e mutations; the handover mutation is caught; the full
  unit and `--e2e` runs have not been redone since the earlier commits: do that in step 6).

- App log (`d507eef`, `5ea7453`): `%USERPROFILE%\.cache\pi-gpui\logs\pi-gpui.log` gets the app's stderr plus
  every pi's and plugin's stderr, timed; menu "⋯ > Show App Log"; session traces carry its last 300 lines; a failed
  smoke run prints each app's log problems (`de26619`). **After Daniel tests something, read this log first.**
- 50-subagent test (2026-11-27): only 2 of 50 showed in the panel. Cause: the publisher in pi-durable-subagents
  stopped at the first subagent it could not follow. Fixed in `7cf8e4e` on branch `pi-gpui-subagents-panel` of
  `C:\Users\Daniel\source\pi-durable-subagents` pushed to the fork sneccc/pi-durable-subagents. Then fixed
  50 extra parent turns: reports go out bundled when the parent is idle (`b71d255`), fetched results get no notice
  (`54ab284`), and Windows EPERM on fleet.json no longer shows an error (`1869322`). Real Haiku 5.5 runs: 3 turns,
  0 notices (results fetched); 4 turns, 2 notices carrying 36 + 14 reports (told to wait). Before: 53 turns.
  Known flakes in that repo on Windows: 4 tests fail before and after (symlinks, SIGKILL, CLI); ui.test.ts flaked once.

## TODO, in order

1. ~~Fix the in-progress single-instance work~~ done (`6ab850c`). ~~Headless testing/debugging~~ done (`097ab76`).
2. ~~URL scheme + `/gpui`~~ done (`876e0f5`, `aaa1e68`), pushed. Verified: registry unit test (temp key), a real link opened
   through the Windows shell is handed to a headless app; `node --no-warnings --test packages/pi-gpui/index.test.ts`
   (4 tests; the waiter test fails when the wait is broken). Daniel tested by hand on 2026-11-27 and all of it worked:
   window buttons, Ctrl+O, a prompt, paste from Explorer, second launch (also minimized, also folder dropped on
   run.bat), no pi left after Ctrl+W/Ctrl+Q, `/gpui` from a terminal pi (app closed and open), Win+R `pi-gpui://` link. The dev registration currently points `pi-gpui://` at `target\release\pi-gpui.exe`;
   `--unregister-url-scheme` when done. Package version not bumped (publishing is outward-facing: ask first).
2b. ~~Terminal pi and app on the same session~~ done (`d2b2ebd`, `79b0e42`): both sides hold `<session>.jsonl.lease`
   (bridge for the app's pi with `appPid`; the pi-gpui package for a terminal pi); the app refuses a held session, the
   terminal warns / asks before `/resume`. Chose pid + `startedAt` over a real OS lock (node's lock would need
   `node:sqlite`, experimental warning in the TUI). Verified: 16 node tests, 4 Rust unit tests, `windows-smoke.py --only
   lease`, mutation sets `--node` (7) and 2 unit ones. NOT yet: Daniel trying a real terminal pi + app on one session.
   2026-11-27 also: `/gpui` typed mid-run (pi already defers shutdown until idle; message now says so, `9faff18`),
   real-terminal test `windows-smoke.py --only gpui`; found and fixed a lease gap: a new session had no lease during
   its first run (`821e3b3`, file-appearance poll). Red tint on sidebar rows a terminal pi holds (`0ef0245`, polls
   lease files every 3 s; `--only held`). Tools: `script/debug/terminal.py` (ConPTY), `slow-model.ts` (faux model).
   Sidebar reload button per project done (`ea15cd2`): sessions made by a terminal pi appear on click (Daniel asked
   for manual; automatic refresh on focus / every few s with a cheap folder fingerprint is the possible next step).
   Later: (a) app watches an open session's `.jsonl` and warns when it grows without our pi writing (catches a terminal
   pi without the package); (b) also probe pi-durable-subagents' `<sha256(session id)>.sqlite.owner` lock; (c) maybe show
   a session's durable subagents in the app (read-only SQLite).
3. **Distribution.**
   - ~~Exe icon and version resource~~ done (`b08069f`): `build.rs` + `embed-resource` (already in the tree via gpui, so
     `Cargo.lock` only gains one line; `cargo add` would have bumped it: pin `"3"`), `.rc` template filled from Cargo.toml,
     `.ico` from `resources/linux/pi-gpui.png` (magick renders `AppIcon.svg` wrongly). gpui loads icon ID 1.
   - `script/bundle-windows.ps1` (zip with exe + LICENSE + README) and an `install.ps1` (copy to
     `%LOCALAPPDATA%\Programs\pi-gpui`, Start menu shortcut, register the URL scheme).
   - GitHub Actions on the fork: a Windows job (fmt, clippy, test; the smoke test only if a runner can show a window and
     install pi), and a release workflow that builds the zip on a tag. Push to the fork and watch it with `gh run watch`.
   - README and `docs/`: replace "Windows isn't supported" for the fork; document install and the test scripts.
   - Updater: currently inert on Windows (the exe is not in a `*.app\bin` layout, so `running_bundle()` is `None`). That is
     safe. Decide whether to build a zip updater or leave updates manual.
4. **Paths and pi compatibility.**
   - UNC paths: check how pi names session folders for `\\server\share\dir`. pi's code strips ONE leading separator;
     `encode_workspace` trims all of them. Compare with pi's source under
     `C:\Users\Daniel\.pi\agent\...\node_modules\@earendil-works\pi-coding-agent` and match it exactly. `strip_verbatim`
     leaves `\\?\UNC\server\share` alone; it should become `\\server\share`. Add tests. Long paths: test.
   - The sidebar shows "pi 1.6.1 - not tested yet" (`TESTED_PI` in `src/pi.rs`; `MIN_PI` is 1.4). Exercise the app against
     1.6.x (slash commands, `/model`, compaction, fork, tree, extension questions, the bridge extension in
     `extensions/pi-gpui-bridge.ts`) before bumping `TESTED_PI`. One tiny prompt ("reply with exactly: pong") worked earlier.
4b. **Plugins on Windows** (2026-11-27): Ask, Secrets and DOOM all work (`script/debug/plugin-gallery.py` renders them
   into `.scratch/shots/plugins.png`; `windows-smoke.py --only ask` checks Ask end to end). Found:
   - DOOM's `plugin.json` runs `./run.sh`, which Windows cannot start. Needs a Windows launcher (e.g. a `run.cmd`
     that makes the venv with `py -3.12`; ViZDoom has no Python 3.14 wheel) and per-OS `run` in plugin.json or a
     `.cmd` lookup in `resolve_program`. The gallery uses `.scratch/doom-venv` (py 3.12, `pip install vizdoom pillow`).
   - Markdown eats backslashes before punctuation in Windows paths: `C:\x\secrets-home\.pi\agent` shows as
     `secrets-home.pi\agent` (CommonMark escape `\.`). Seen in the Secrets tool result. Consider rendering
     tool-result text as plain text, or not treating `\` + punctuation as an escape inside path-like words.
5. **Paths not yet exercised on Windows.** Add a test or at least a manual pass, and record the result here:
   reveal in Explorer and open in editor (use `spawn_outliving`; check an Explorer window appears), Trash through the real
   delete-session flow (the confirm is a native prompt, drive it like the folder dialog, see below), plugins (`docs/PLUGINS.md`,
   the examples in `examples/plugins`; the earlier runs always printed `loaded 0 plugin(s)`), the pi update panel
   (`pi update`, `pi list`, `curl` for versions), drag and drop of files (GPUI `ExternalPaths`), HiDPI 125/150 % (only the
   maths is unit-tested), multi-monitor, IME.
6. **Finish.** Update the "Tests" section of `docs/WINDOWS_UI_CHECKS.md` (new smoke checks: caption buttons, crash, paste,
   handover), run `mutation-check.py` and `--e2e`, push to `fork`, and ask Daniel about the fork's `main`.

## Things that cost time, so you do not repeat them

**gpui 0.2.2 on Windows**
- `Window::dispatch_event` is public but returns a private type, so it cannot be called from outside gpui. Mouse
  automation posts `WM_MOUSEMOVE/LBUTTONDOWN/LBUTTONUP` instead.
- gpui's hit test (`window_control_area`) uses the **last known mouse position**, not the message coordinates. Post a
  `WM_MOUSEMOVE` first, then `WM_NCHITTEST`.
- Control areas are matched in paint order, so a parent marked `Drag` shadows child `Min/Max/Close` areas.
- A non-client mouse down counts as "handled" if any focusable element is under the pointer (they call `prevent_default`);
  then gpui never minimizes/maximizes/closes by itself. The app runs those actions from its own click handlers.
- Non-client mouse **up** events carry `click_count: 1` always; detect a double click on the **down** event.
- `Window::zoom_window` only maximizes on Windows; `platform::toggle_zoom` posts `SC_RESTORE`/`SC_MAXIMIZE`.
- `Window::window_handle` is gpui's own method; use `HasWindowHandle::window_handle(window)` for the raw handle.
- Windows' foreground lock stops a background-launched app from taking focus, so "automation does not take the focus"
  in the smoke test is best effort and cannot be mutation-tested.

**App behaviour**
- Launching the app **with a folder** starts a session by itself, so there is already one pi before Ctrl+N.
  Track processes per session in tests.
- Clicking the empty transcript takes the focus off the chat; then Ctrl+W no longer reaches the session.
- Closing a session is clean (stdin closes, the whole `cmd.exe -> node.exe` tree exits). Quitting is clean too.
- `ButtonLike`/`IconButton` call `prevent_default` on mouse down; do not use them for caption buttons.
- The native "Select Folder" dialog is a separate window: `shot` does not show it and automation clicks do not reach it.
  Drive it from outside: find `#32770` "Select Folder", `WM_SETTEXT` into its Edit control, `BM_CLICK` the OK button (id 1).
  `SendKeys` did not work on it.

**Tooling quirks in the pi bash tool (this machine)**
- Backslashes in a bash command are halved once (`\\n` arrives as `\n` and becomes a real newline inside a Python string).
  Do not generate Rust or Python source through bash heredocs that contain backslashes. Use the **write** tool for scripts
  and the **edit** tool for patches; use `chr(92)` in Python when you must build a backslash.
- A `read` issued in the same batch as the command that creates the file races it: read in a separate step.
- Python needs `-I` (project rule). `cargo` is at `C:\Users\Daniel\.cargo\bin` (add it to `PATH` in the shell).
- Files in this repo are written with LF; git warns "LF will be replaced by CRLF" (autocrlf), which is harmless.
- The old helpers (hit test, non-client click, process tree, window list, folder dialog driver) are now subcommands of
  `script/debug/app.py`; the originals are archived under `.scratch/archive/`.
- `python -I` drops the script's folder from `sys.path`: scripts in `script/` cannot import each other. Keep each self-contained.

**Headless and process checks**
- On a freshly created desktop Windows shows its input-language indicator (`UAC_InputIndicatorOverlayWnd`,
  `UAC Input Indicator`) as windows of the app's process. Not ours; the smoke test ignores them.
- Walking `ParentProcessId` alone is wrong: a stale parent pid can match a new process, which then "adopts" unrelated
  processes. `descendants()` in the smoke test now requires a child to start after its parent (`GetProcessTimes`).
  That was the cause of a bogus "crash: killing the app also stops pi" failure.
- A running pi-gpui.exe cannot be replaced by `cargo build`, but it can be renamed: `mv target/release/pi-gpui.exe
  target/release/pi-gpui.exe.running-old` keeps Daniel's app running and lets the build go on. Note: a new launch then hands
  over to the OLD running app (single instance), so Daniel sees old behaviour until he quits it.
- `script/debug/app.py start` launches the app detached and breaking away from the tool's job, so it survives between
  tool calls; always `stop` it before `cargo build --release`.

## Key files

- `src/automation.rs`, `src/automation/{macos,windows}.rs`: the automation protocol (macOS code untouched, moved).
- `src/process.rs`: `command`, `kill_tree`, `spawn_outliving`, `contain_children` (job object) and their tests.
- `src/single_instance.rs`: Linux socket, Windows named pipe.
- `src/platform.rs`: Recycle Bin, Explorer, clipboard files, `toggle_zoom`, `restore_if_minimized`, `enter_headless_desktop`.
- `src/hub.rs`: title bar, `caption_buttons`, `render_window_controls`.
- `src/sessions.rs`: `canonical_path`, `strip_verbatim`, `encode_workspace`, `process_is_alive`.
- `src/pi.rs`: pi discovery (`find_executable`, `executable_names`), versions, spawn.
- `packages/pi-gpui/index.ts`: the `/gpui` pi extension (still macOS/Linux only).
- `docs/WINDOWS_UI_CHECKS.md`, `script/windows-smoke.py`, `script/mutation-check.py`, `script/ui-command.py`, `script/debug/` (headless debug tool + README), `run.bat`.
