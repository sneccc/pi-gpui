# Grill plugin

Shows grilling interviews as forms in the chat. Made for Matt Pocock's skills
([mattpocock/skills](https://github.com/mattpocock/skills)): `grilling`,
`grill-me` and `grill-with-docs` ask the user a round of questions at a time,
each with a recommended answer. Without this plugin the agent writes each
round as text and you reply in a message; with it, every round is a form:

- one question at a time, with a `2 / 4` counter, Back / Next, and the round
  and progress in the title (`Grilling · round 2 · 3 settled, 2 open`);
- the agent's recommendation first: "Yes, as recommended" for yes/no
  questions, or the recommended choice marked "(Recommended)";
- a field to answer in your own words (press Down to reach it);
- Esc stops the round; the agent then stops grilling until you ask again.

The agent gets one result per round: which recommendations you accepted,
what you chose instead, and what you wrote.

## How it works

The plugin ships a pi tool, `grill_round`. Its instructions (in pi's system
prompt while the tool is loaded) tell the agent to ask each grilling round
with it instead of writing the questions as text, so the skills themselves
stay unchanged. When pi calls the tool, the plugin opens a `prompt.form` in
that tool call and hands the answers back through a file the tool waits for.

## Install

Copy this folder to `~/.config/pi-gpui/plugins/grill` (Windows:
`%USERPROFILE%\.config\pi-gpui\plugins\grill`; macOS:
`~/Library/Application Support/pi-gpui/plugins/grill`). Restart Pi GPUI and
start a new session (or reload one with Ctrl+Shift+R / ⌘⇧R). Needs Node.js.

Then ask for a grilling, e.g. `/skill:grill-me` followed by your plan.

## Files

- `plugin.json`: the manifest.
- `pi-extension.ts`: the `grill_round` tool (loaded into every pi the app starts).
- `plugin.mjs`: opens the form and passes the answers on.
- `format.mjs`: the form layout and the answer summary (shared, no dependencies).
- `grill.test.ts`: `node --no-warnings --test examples/plugins/grill/grill.test.ts`.
