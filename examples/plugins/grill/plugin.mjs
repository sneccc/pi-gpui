// Pi GPUI plugin: a grilling round as a form inside the chat.
//
// Protocol (docs/PLUGINS.md): the app sends `tool.start` when pi calls
// `grill_round`. We open a `prompt.form` in that tool call, one question at a
// time, with the agent's recommendation as the first option; the answers come
// back as `prompt.result` and go to <tmp>/pi-gpui-grill/<callId>.json, where
// the tool waits.
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import readline from "node:readline";
import { roundForm } from "./format.mjs";

const ANSWERS_DIR = join(tmpdir(), "pi-gpui-grill");
const pending = new Set();

const send = (method, params = {}) => process.stdout.write(`${JSON.stringify({ method, params })}\n`);
const answerPath = (callId) => join(ANSWERS_DIR, `${callId.replace(/[^A-Za-z0-9_.-]/g, "_")}.json`);

function answer(callId, result) {
	mkdirSync(ANSWERS_DIR, { recursive: true });
	const path = answerPath(callId);
	writeFileSync(`${path}.tmp`, JSON.stringify(result));
	renameSync(`${path}.tmp`, path); // in one step: the tool never reads half a file
}

function ask(callId, args) {
	const form = roundForm(callId, args);
	if (!form) return; // the tool rejects the call itself
	pending.add(callId);
	send("prompt.form", form);
}

function finish(params) {
	if (!pending.delete(params.id) || params.closed) return; // not ours, or nobody waits
	answer(params.id, {
		status: params.cancelled ? "cancelled" : "answered",
		answers: Array.isArray(params.answers) ? params.answers : [],
		error: params.error,
	});
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
	let message;
	try {
		message = JSON.parse(line);
	} catch {
		return;
	}
	const params = message.params ?? {};
	try {
		if (message.method === "tool.start" && params.tool === "grill_round") {
			ask(params.callId, params.args);
		} else if (message.method === "prompt.result") {
			finish(params);
		}
	} catch (error) {
		send("notify", { message: `Grill: ${error.message}`, tone: "error" });
	}
});
