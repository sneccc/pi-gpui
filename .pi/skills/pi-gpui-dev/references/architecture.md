# Architecture map

## Processes

```
pi-gpui.exe ── per open session: pi --mode rpc --extension <bridge> [--session file]
     │            (on Windows: pi.cmd → cmd.exe → node.exe; all in a kill-on-close job)
     ├── plugins: one process each (JSON lines), started on first use
     └── second launches: hand their folder / pi-gpui:// link over a named pipe and exit
terminal pi (with the pi-gpui package) ── /gpui hands its session to the app
```

- The app talks to pi over stdin/stdout JSON lines (pi's RPC protocol,
  docs in pi's `docs/rpc.md`). Commands have an `id`; pi answers with
  `{type: "response", command, id, data}` and streams events.
- pi's own UI requests reach the app as `extension_ui_request`
  (`select`, `confirm`, `input`, `editor`, `notify`, `set_editor_text`,
  `setStatus`, …): `chat.rs` `apply_extension_request` and `chat/dialog.rs`.
- Things RPC lacks are filled by the bridge extension
  (`extensions/pi-gpui-bridge.ts`): tree navigation (`/pi-gpui-navigate <id>`),
  usage details via `setStatus("pi-gpui:usage")`, and the session lease.

## Source files

| File | What |
|---|---|
| `main.rs` | start-up: CLI flags, single instance, headless desktop, window, menus |
| `hub.rs` (~5k lines) | the window: title bar, sidebar (projects, sessions, reload button, red held rows), tabs, notices, opening sessions, plugin requests |
| `chat.rs` (~4k) | one session: rows (transcript), composer, pi process, forks, tree navigation, message edit/recall, plugin views |
| `chat/` | `dialog` (pi questions), `tree` (/tree view), `slash` (commands menu), `usage` (context popover), `plugin_*` (forms, prompts, views), `remote` (plugin `sessions.*`), `log_viewer` |
| `text_input.rs` | the text field: editing, IME, attachments as chips, undo, message history (Up/Down) |
| `pi.rs` | finding pi (`find_executable`), versions (`MIN_PI`, `TESTED_PI`), spawning, the bridge file |
| `sessions.rs` | pi's session folder, titles for the sidebar, leases (`foreign_lease_holder`), paths (`canonical_path`) |
| `plugins.rs` | loading `plugin.json`, starting plugins, routing messages |
| `platform.rs` | OS pieces: Recycle Bin, Explorer, clipboard files, window zoom/restore, headless desktop, console attach |
| `process.rs` | `command` (no console window), `kill_tree`, `spawn_outliving`, `contain_children` (job object) |
| `single_instance.rs` | Unix socket / Windows named pipe handover |
| `url_scheme.rs` | `--register-url-scheme` (HKCU) |
| `automation.rs` + `automation/` | `PI_GPUI_AUTOMATION`: keys, clicks, shots for tests |
| `settings.rs` | settings.json, watched and applied live |
| `test_support.rs` | `TempDir` for unit tests |

## Where state lives

- App data: `%HOME%\.config\pi-gpui` on Windows (`HOME`, else `USERPROFILE`;
  `XDG_CONFIG_HOME` wins), `~/Library/Application Support/pi-gpui` on macOS:
  `state.json` (projects), `settings.json`, `themes/`, `plugins/`,
  `extensions/pi-gpui-bridge.ts` (written from the exe).
- pi's sessions: `PI_CODING_AGENT_DIR` (default `~/.pi/agent`) `/sessions/--<encoded cwd>--/*.jsonl`;
  leases beside them as `<session>.jsonl.lease`.

## Adding things

- A new UI feature for the agent: often a plugin (docs/PLUGINS.md), not app code.
- Something pi does not expose over RPC: a bridge command (keep the bridge's
  lease block identical to the package's; `extensions/lease.test.ts` checks it).
- Rust style: see docs/GPUI_STYLE_GUIDE.md; comments explain why, in plain sentences.
