/**
 * A pi extension for tests: the faux model `faux/faux-1` follows a script
 * instead of thinking. Free, no login, the same every time.
 *
 *     PI_SCRIPTED_MODEL=steps.json pi --offline -e script/debug/scripted-model.ts \
 *         --provider faux --model faux-1 --thinking off
 *
 * steps.json is a list, one step per model reply, in order:
 *     {"text": "hello"}                          a plain answer
 *     {"tool": "ask_user_question", "args": {...}}  calls a tool (and stops for its result)
 *     {"echo": "GOT: "}                          answers with the last tool result's text
 * After the last step it repeats the last one. Each step may also set
 * "delayMs" to answer slowly.
 */
import { readFileSync } from "node:fs";
import { fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";

type Step = { text?: string; tool?: string; args?: Record<string, unknown>; echo?: string; delayMs?: number };

/** The text of the newest tool result in the conversation. */
function lastToolResult(context: any): string {
	const messages: any[] = context?.messages ?? [];
	for (let index = messages.length - 1; index >= 0; index -= 1) {
		const message = messages[index];
		if (message?.role !== "toolResult") continue;
		const content = Array.isArray(message.content) ? message.content : [];
		return content.map((part: any) => (part?.type === "text" ? part.text : "")).join("");
	}
	return "(no tool result)";
}

export default function (pi: any) {
	const file = process.env.PI_SCRIPTED_MODEL;
	const steps: Step[] = file ? JSON.parse(readFileSync(file, "utf8")) : [{ text: "PI_SCRIPTED_MODEL is not set" }];
	const faux = fauxProvider();
	let next = 0;
	const reply = async (context: any) => {
		const step = steps[Math.min(next, steps.length - 1)];
		next += 1;
		faux.appendResponses([reply]);
		if (step.delayMs) await new Promise((resolve) => setTimeout(resolve, step.delayMs));
		if (step.tool) {
			return fauxAssistantMessage(fauxToolCall(step.tool, step.args ?? {}), { stopReason: "toolUse" });
		}
		if (step.echo !== undefined) return fauxAssistantMessage(`${step.echo}${lastToolResult(context)}`);
		return fauxAssistantMessage(step.text ?? "");
	};
	faux.setResponses([reply]);
	pi.registerProvider(faux.provider);
}
