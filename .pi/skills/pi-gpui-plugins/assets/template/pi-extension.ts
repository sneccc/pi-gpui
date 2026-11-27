/**
 * The agent side of the Hello plugin: a pi tool. The app loads this file into
 * every pi it starts (contributes.piExtensions). The tool draws nothing: the
 * app sees the call, the plugin shows a field in it, and the answer arrives
 * as a file this tool waits for (a file watch, not a busy loop).
 */
import { Type } from "@earendil-works/pi-ai";
import { mkdirSync, readFileSync, unlinkSync, watch } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const ANSWERS = join(tmpdir(), "pi-gpui-hello");

function waitForAnswer(callId: string, signal?: AbortSignal): Promise<{ value?: string; cancelled?: boolean } | null> {
	mkdirSync(ANSWERS, { recursive: true });
	const path = join(ANSWERS, `${callId.replace(/[^A-Za-z0-9_.-]/g, "_")}.json`);
	return new Promise((resolve) => {
		const read = () => {
			try {
				const answer = JSON.parse(readFileSync(path, "utf8"));
				unlinkSync(path); // leave no temp files behind
				finish(answer);
			} catch {}
		};
		const watcher = watch(ANSWERS, read);
		const poll = setInterval(read, 2000); // safety net if a file event is missed
		const finish = (answer: any) => {
			watcher.close();
			clearInterval(poll);
			signal?.removeEventListener("abort", stop);
			resolve(answer);
		};
		const stop = () => finish(null);
		signal?.addEventListener("abort", stop, { once: true });
		read();
	});
}

export default function (pi: any) {
	pi.registerTool({
		name: "hello_ask_name",
		label: "Ask Name",
		description: "Ask the user for their name in a field in the chat and return it.",
		parameters: Type.Object({
			question: Type.Optional(Type.String({ description: "The question to show." })),
		}),
		executionMode: "sequential",
		async execute(toolCallId: string, _params: unknown, signal?: AbortSignal) {
			const answer = await waitForAnswer(toolCallId, signal);
			const text = !answer || answer.cancelled ? "The user did not answer." : `The user's name is ${answer.value}.`;
			return { content: [{ type: "text", text }], details: {} };
		},
	});
}
