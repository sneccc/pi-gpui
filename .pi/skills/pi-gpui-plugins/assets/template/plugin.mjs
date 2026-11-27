// The app side of the Hello plugin (docs/PLUGINS.md, protocol 1).
// - A composer button / keybinding that inserts text (command.run).
// - When pi calls `hello_ask_name`, a text field in that tool call
//   (tool.start -> prompt.input -> prompt.result). The answer is handed to
//   the tool through a file the tool waits for (see pi-extension.ts).
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import readline from "node:readline";

const ANSWERS = join(tmpdir(), "pi-gpui-hello");
const send = (method, params = {}) => process.stdout.write(`${JSON.stringify({ method, params })}\n`);
const answerPath = (callId) => join(ANSWERS, `${callId.replace(/[^A-Za-z0-9_.-]/g, "_")}.json`);

function answer(callId, result) {
	mkdirSync(ANSWERS, { recursive: true });
	const path = answerPath(callId);
	writeFileSync(`${path}.tmp`, JSON.stringify(result));
	renameSync(`${path}.tmp`, path); // in one step: the tool never reads half a file
}

const open = new Set(); // calls with an open field

readline.createInterface({ input: process.stdin }).on("line", (line) => {
	let message;
	try {
		message = JSON.parse(line);
	} catch {
		return;
	}
	const { method, params = {} } = message;
	if (method === "command.run" && params.command === "hello.insert") {
		send("composer.insert", { text: "Hello from a plugin", spaceBefore: true });
	} else if (method === "tool.start" && params.tool === "hello_ask_name") {
		open.add(params.callId);
		send("prompt.input", {
			id: params.callId,
			callId: params.callId,
			title: params.args?.question || "What is your name?",
			placeholder: "Type and press Enter",
		});
	} else if (method === "prompt.result" && open.delete(params.id) && !params.closed) {
		answer(params.id, params.cancelled ? { cancelled: true } : { value: params.value ?? "" });
	}
});
// stdin closes when the app quits: readline ends and node exits by itself.
