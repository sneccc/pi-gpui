# Recipes

One recipe per need. Message names and fields are from docs/PLUGINS.md
(protocol 1); check there for every option. `send(method, params)` writes one
line: `{"method": ..., "params": ...}`.

## A button or shortcut

```json
"contributes": { "commands": [ { "id": "hello.insert", "title": "Insert a greeting",
  "icon": "sparkle", "keybinding": "cmd-shift-h", "placement": ["composer"] } ] }
```

- The button shows in the message box before the plugin runs; the first press
  starts the plugin and sends `command.run` with `command`, `workspace?`, `session?`.
- Icons: `microphone`, `terminal`, `file`, `sparkle`, `message`, `tool`,
  `folder`, `refresh`, `check`, `info`, `warning`.
- A toggle (recording, on/off): `command.setState {command, active}`.
- Template: `assets/template` (verified: shortcut and click both work, tooltip
  shows "Ctrl+Shift+H" on Windows).

## Text into the message box

- `composer.insert {text, spaceBefore?, replaceLast?}` at the cursor of the
  active message field. `replaceLast` replaces your previous insertion (live
  partial results, e.g. dictation).
- `composer.setText {text}` replaces everything.

## A status item in the title bar

`status.set {id, text, tone?, busy?}` (tone: info, warning, error, success;
busy shows a spinner), `status.clear {id}`. Send on change only.

## Ask the user while the agent waits

The pattern behind Ask, Secrets and the template:

1. pi extension: `registerTool` with `executionMode: "sequential"`; `execute`
   waits for `<tmp>/<plugin>/<callId>.json` (file watch + a 2 s poll as safety
   net), reads it, deletes it, returns the answer as text.
2. `plugin.json`: the tool's name in `contributes.toolEvents`.
3. Plugin: on `tool.start` send a prompt with `callId` (and `id`, usually the
   same); on `prompt.result` write the file (`.tmp`, then rename).

Prompt kinds:
- `prompt.input {id, callId, title, description?, placeholder?, submitLabel?, secret?}`:
  one text field. `secret: true` shows dots, no copy, an eye to reveal; the
  value goes only to the plugin. Answer: `prompt.result {id, value}`.
- `prompt.form {id, callId, questions, title?, submitLabel?}`: up to 20
  questions, one at a time with Back/Next. Question: `title`, `id?`,
  `description?`, `options?` (strings or `{label, description?}`, up to 20),
  `multiple?`, `allowText?`, `required?` (default true), `placeholder?`,
  `selected?` (option indexes picked when the form opens: put the
  recommended answer there so Enter accepts it).
  Answer: `answers: [{id, selected, labels, text?}]`, also on cancel (what was
  given so far, with `cancelled: true`).
- Keys in a form: Up/Down move, Space or a digit picks, Enter goes on,
  Left/Right change the question without answering, Esc cancels. Expand
  (next to Cancel) shows all questions; Up/Down then flow across questions. To type an own answer, move
  to the text field first (Down): Space on a highlighted option picks it.
- A bigger example with recommendations first and a summary for the agent:
  `examples/plugins/grill` (grilling skills).

Never send the secret itself back to pi (see examples/plugins/secrets: it
returns only where the value was saved).

## Show something live in a tool call

- `contributes.toolViews: [{tool, title, width, height}]`.
- On `view.open {tool, callId, args}` stream `view.frame {callId, data, mimeType}`
  (base64 JPEG for video, PNG for flat UI) at the size shown. Only the newest
  call of the tool receives frames; older rows keep their last frame.
- Clicking the view gives it the keyboard: `view.focus {callId, focused}`, then
  `view.key {callId, key, down}`; Esc gives it back. Release held keys when
  focus goes away; do work only while needed.
- Example: examples/plugins/doom.

## Drive pi sessions

`"permissions": ["sessions"]`, then requests (with an `id`, answered with
`result` or `error`): `sessions.list`, `open`, `get`, `focus`, `prompt`
(`mode: "followUp"` queues), `abort`, `answer` (pi's extension questions),
`rpc` (reads and settings; no prompts or session switches), `rename`,
`reload`, `subscribe` / `unsubscribe` (events: `session.events`,
`session.state`, `session.closed`).

## Settings

`contributes.settings: [{key, title, description?, type, default?, ...}]`,
type `string` (`secret`), `number` (`min`, `max`), `boolean`, `select`
(`options`). Stored as `"<id>.<key>"` in the app's settings.json. Values
arrive in `initialize` and `settings.changed`; `settings.get` / `settings.set`.

## Links to other sessions

Return `details: { piGpui: { sessionLinks: [{file, label?}] } }` from a tool,
or for tools you don't own: `contributes.sessionLinks: [{tool, field, label}]`
(`field` like `details.results.sessionFile`).

## Updates and restart

`"permissions": ["app"]`: `app.checkUpdates`, `app.restart {force?}`.

## Not possible today (protocol wishes)

Panels, tabs, sidebars, changing the layout or the theme through the
protocol. A plugin or pi could edit the settings file (the app applies it
live), but that is not a plugin API. Record new wishes in `lessons.md`.
