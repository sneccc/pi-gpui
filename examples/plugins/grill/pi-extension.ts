/**
 * The agent's side of the Grill plugin: a `grill_round` tool for grilling
 * sessions (Matt Pocock's `grilling`, `grill-me` and `grill-with-docs`
 * skills). Instead of writing a round of questions as text, the agent passes
 * them here; Pi GPUI shows them as a form in the chat (one question at a time,
 * the recommended answer first) and the answers come back as the result.
 *
 * The tool draws nothing itself: the app sees the call and the plugin
 * (plugin.mjs) opens the form; it leaves the answers in a file this tool
 * waits for.
 */
import { Type } from "@earendil-works/pi-ai";
import { mkdirSync, readFileSync, unlinkSync, watch } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
// @ts-ignore: plain JavaScript, shared with plugin.mjs and the tests
import { describeRound } from "./format.mjs";

const ANSWERS_DIR = join(tmpdir(), "pi-gpui-grill");
/** Safety net in case a file event is missed; events do the real work. */
const FALLBACK_POLL_MS = 2000;
const MAX_QUESTIONS = 12;

type FormAnswer = { id: string; selected: number[]; labels: string[]; text?: string };
type Outcome = { status: "answered" | "cancelled"; answers: FormAnswer[]; error?: string };
type Question = { title: string; body?: string; choices?: string[]; recommended: string; recommendedChoice?: number };

const answerPath = (callId: string) => join(ANSWERS_DIR, `${callId.replace(/[^A-Za-z0-9_.-]/g, "_")}.json`);

function waitForOutcome(callId: string, signal?: AbortSignal): Promise<Outcome | null> {
	mkdirSync(ANSWERS_DIR, { recursive: true });
	const path = answerPath(callId);
	return new Promise((resolve) => {
		let watcher: ReturnType<typeof watch> | undefined;
		const finish = (outcome: Outcome | null) => {
			watcher?.close();
			clearInterval(poll);
			signal?.removeEventListener("abort", abort);
			resolve(outcome);
		};
		const check = () => {
			try {
				const outcome = JSON.parse(readFileSync(path, "utf8")) as Outcome;
				unlinkSync(path);
				finish(outcome);
			} catch {
				// Not there yet.
			}
		};
		const abort = () => finish(null);
		const poll = setInterval(check, FALLBACK_POLL_MS);
		if (signal?.aborted) return abort();
		signal?.addEventListener("abort", abort, { once: true });
		try {
			watcher = watch(ANSWERS_DIR, check);
		} catch {
			// No file events here; the poll still answers.
		}
		check();
	});
}

export default function (pi: any) {
	pi.registerTool({
		name: "grill_round",
		label: "Grill Round",
		description: `Ask one round of a grilling interview (the grilling, grill-me and grill-with-docs skills) as a form in the chat, and wait for the answers. Pass every question of the current frontier, up to ${MAX_QUESTIONS}, each with your recommended answer. The user accepts the recommendation, picks a choice, or answers in their own words.`,
		promptSnippet: "Ask a round of grilling questions (each with a recommended answer) as a form",
		promptGuidelines: [
			"When you grill the user (the grilling, grill-me or grill-with-docs skills), ask every round with grill_round instead of writing the questions in your message. Put all questions of the round in one call.",
			"In grill_round, give each question your recommended answer in `recommended`, worded so that accepting it means 'yes'. Use `choices` only when there are real alternatives, and point `recommendedChoice` at the one you recommend.",
			"After grill_round returns, update the design tree with the decisions and call grill_round again for the next round. Keep a short summary of settled decisions in `progress`.",
		],
		parameters: Type.Object({
			round: Type.Number({ description: "Round number, starting at 1." }),
			progress: Type.Optional(
				Type.String({ description: "One line on where the grilling stands, e.g. '3 decisions settled, 2 open'." }),
			),
			questions: Type.Array(
				Type.Object({
					title: Type.String({ description: "Short question title." }),
					body: Type.Optional(Type.String({ description: "The question in full, with context. Plain text." })),
					choices: Type.Optional(Type.Array(Type.String(), { description: "Real alternatives to pick from, if any." })),
					recommended: Type.String({ description: "Your recommended answer, worded so 'yes' accepts it." }),
					recommendedChoice: Type.Optional(
						Type.Number({ description: "Index into choices of the recommended one, when choices are given." }),
					),
				}),
				{ minItems: 1, maxItems: MAX_QUESTIONS },
			),
		}),
		executionMode: "sequential",

		async execute(toolCallId: string, params: any, signal?: AbortSignal) {
			const questions: Question[] = params.questions.filter((question: Question) => question.title?.trim());
			if (questions.length === 0) throw new Error("grill_round needs at least one question.");
			const outcome = await waitForOutcome(toolCallId, signal);
			if (!outcome) {
				return {
					content: [{ type: "text", text: "The round was not answered: the run was stopped." }],
					details: { status: "cancelled", answers: [] },
				};
			}
			if (outcome.error) throw new Error(`The form could not be shown: ${outcome.error}`);
			return {
				content: [{ type: "text", text: describeRound(params.round, questions, outcome) }],
				details: { status: outcome.status, answers: outcome.answers },
			};
		},
	});
}
