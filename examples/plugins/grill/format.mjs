// The Grill plugin's pure parts, shared by plugin.mjs (the form) and
// pi-extension.ts (the answers for the agent). No dependencies, so the tests
// run in plain node: node --no-warnings --test examples/plugins/grill/

/**
 * The form's question for one grilling question. Option 0 is always the
 * recommendation, so the answer tells "accepted" from "chose another".
 */
export function formQuestion(question, index) {
	const title = `Q${index + 1} · ${String(question.title ?? "").trim()}`;
	const body = String(question.body ?? "").trim();
	const recommended = String(question.recommended ?? "").trim();
	const choices = Array.isArray(question.choices) ? question.choices.map(String) : [];
	let options;
	if (choices.length > 0) {
		const best =
			Number.isInteger(question.recommendedChoice) && choices[question.recommendedChoice] !== undefined
				? question.recommendedChoice
				: 0;
		options = [
			{ label: `${choices[best]} (Recommended)`, description: recommended || undefined },
			...choices.filter((_, position) => position !== best).map((label) => ({ label })),
		];
	} else {
		options = [{ label: "Yes, as recommended", description: recommended }];
	}
	return {
		id: String(index),
		title,
		description: body || undefined,
		options,
		// The recommendation is picked from the start: Enter accepts it.
		selected: [0],
		allowText: true,
		placeholder: "Or answer in your own words",
	};
}

/** The whole form for a `grill_round` call, or null when it has no questions. */
export function roundForm(callId, args) {
	const questions = (Array.isArray(args?.questions) ? args.questions : [])
		.filter((question) => String(question?.title ?? "").trim())
		.map(formQuestion);
	if (questions.length === 0) return null;
	const round = Number(args?.round) || 1;
	const progress = String(args?.progress ?? "").trim();
	return {
		id: callId,
		callId,
		title: `Grilling · round ${round}${progress ? ` · ${progress}` : ""}`,
		questions,
		submitLabel: "Send answers",
	};
}

/** One answer in words the agent can act on. */
export function describeAnswer(question, answer) {
	const text = answer?.text?.trim();
	const picked = answer?.selected?.[0];
	if (text) return `answered in their own words: ${text.replace(/\n/g, "\n   ")}`;
	if (picked === 0) return `accepted your recommendation (${question.recommended})`;
	if (picked !== undefined && answer?.labels?.[0]) return `chose: ${answer.labels[0].replace(/ \(Recommended\)$/, "")}`;
	return "(no answer)";
}

/** The tool result: every answer, and what the agent should do next. */
export function describeRound(round, questions, outcome) {
	const lines = questions.map(
		(question, index) => `Q${index + 1} ${String(question.title).trim()}\n   ${describeAnswer(question, outcome.answers[index])}`,
	);
	const header =
		outcome.status === "answered"
			? `Round ${round}: the user answered.`
			: `Round ${round}: the user stopped the round. Answers given before that:`;
	const next =
		outcome.status === "answered"
			? "Update the design tree with these decisions, then ask the next round with grill_round (or tell the user the frontier is empty)."
			: "Do not continue grilling until the user asks to.";
	return `${header}\n${lines.join("\n")}\n\n${next}`;
}
