# Windows

The port lives on branch `windows-support` of the fork (`git push fork
windows-support`); upstream `origin` supports macOS and Linux only.

## Environment

- Rust is pinned (`rust-toolchain.toml`); `cargo` is in `C:\Users\Daniel\.cargo\bin`
  (add to PATH in the shell). x86_64-pc-windows-msvc.
- pi: `C:\Users\Daniel\.pi\agent\bin\pi.cmd` (a `.cmd` shim around node).
- Python scripts run with `python -I` (project rule): the script folder is not
  on `sys.path`, so scripts cannot import siblings; load them with
  `importlib.util.spec_from_file_location` or add the path explicitly.
- `HOME` is set on this machine; node's home is `USERPROFILE` regardless.

## gpui 0.2.2 on Windows

- `Window::dispatch_event` returns a private type: mouse automation posts
  `WM_MOUSEMOVE/LBUTTONDOWN/LBUTTONUP` instead.
- gpui's hit test uses the last known mouse position: send `WM_MOUSEMOVE`
  first (SendMessage: synchronous, no sleep), then `WM_NCHITTEST`.
- Control areas match in paint order: a parent marked `Drag` shadows child
  min/max/close areas.
- A non-client mouse down counts as handled if a focusable element is under the
  pointer; gpui then never minimizes/maximizes/closes: the app does it itself.
- Non-client mouse up always has `click_count: 1`: detect double clicks on down.
- `zoom_window` only maximizes: `platform::toggle_zoom` posts SC_RESTORE/SC_MAXIMIZE.
- Raw handle: `HasWindowHandle::window_handle(window)` (gpui's own
  `window_handle` is something else).
- gpui loads the window icon from resource ID 1 (build.rs embeds it).
- `ButtonLike`/`IconButton` call `prevent_default` on mouse down: not for caption buttons.

## Processes

- Children start with `CREATE_NO_WINDOW` (`process::command`), else consoles
  appear and the main window gets minimized.
- `Child::kill` kills only `cmd.exe` of `pi.cmd`: use `process::kill_tree`
  (`taskkill /T`).
- The app joins a kill-on-close job (`contain_children`), so pi dies with the
  app even after a crash; `spawn_outliving` breaks away (editor, Explorer).
- Process ids are reused quickly: never trust a parent pid alone; compare
  start times (`GetProcessTimes`). Leases store `startedAt` for this.

## Headless and focus

- `PI_GPUI_HEADLESS=1`: the window lives on the hidden desktop
  `pi-gpui-headless`; it paints, `shot` works, no focus theft. Tools that look
  at windows must `SetThreadDesktop` onto it (per thread).
- A fresh desktop shows Windows' input indicator windows in the app's process.
- Console windows of children open on the user's normal desktop, not the
  hidden one: look at both when checking for stray windows.
- Windows' foreground lock: focus checks are best effort.
- The native folder dialog is a separate window: drive it with `app.py dialog`.

## Builds

- A running exe can be renamed but not replaced; rename always works, so
  "rename succeeded" proves nothing: always move it aside before building.
- Files are written with LF; git's CRLF warnings are harmless.
- Paths: strip `\\?\` (`sessions::canonical_path`); Markdown in the chat eats
  `\` before punctuation in paths (open issue).
