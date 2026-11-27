# Troubleshooting

Look in the app log first. In a headless test it is `.scratch/headless/app.log`;
a release app on Windows has no console, so start it from the debug tool or
read the notice in the window.

Rows marked (seen) happened in our runs; the others follow the app's own
error messages in `src/plugins.rs`. When you hit one, mark it (seen) and
add what helped.

| Symptom | Likely cause | Fix |
|---|---|---|
| `loaded 0 plugin(s)` (seen) | wrong folder, or `plugin.json` missing | see platforms.md for the folder; the folder name does not matter, `plugin.json` must be inside |
| `plugin not loaded: <reason>` | manifest error (protocol, ids, missing `piExtensions` file) | fix what the reason says; command ids must start with `<id>.` |
| Button shows, nothing happens (seen: DOOM's `./run.sh`) | program did not start: `program "x" not found`, `no such file` | `run` must be findable: a name on PATH (`node`) or `./file` in the plugin folder; Windows cannot start `./run.sh` |
| `plugin <id> exited (...)` | the program crashed | the window shows its last stderr line; run the program by hand and paste a message on stdin |
| `plugin <id> wrote non-JSON` | something printed to stdout | stdout is only for protocol lines; log with the `log` message or stderr |
| pi never calls the tool | the app's pi was started before the plugin was installed, or two extensions register the tool | restart the app (plugins load at start), reload sessions; use a unique tool name |
| Tool runs but no field or form | tool not in `contributes.toolEvents`, or the plugin missed `tool.start` | check the manifest; log `tool.start` in the plugin |
| Field shows, answer never reaches pi | handoff file not written, wrong name, or the tool watches another folder | same temp folder and file name rule on both sides; write `.tmp` then rename |
| Tool waits forever after cancel | the plugin ignores `cancelled` | always write an outcome, also for cancel; ignore only `closed` |
| A path in the result looks wrong (`\.pi` shown as `.pi`) (seen) | Markdown eats `\` before punctuation | put paths in backticks |
| Live view stays empty | frames sent for an older `callId`, or before `view.open` | stream only to the newest `view.open` call |
