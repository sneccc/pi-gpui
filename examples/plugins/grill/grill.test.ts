// node --no-warnings --test examples/plugins/grill/grill.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-ignore: plain JavaScript
import { describeAnswer, describeRound, formQuestion, roundForm } from "./format.mjs";

const open = { title: "Ship on Friday?", body: "The release train leaves at 5.", recommended: "Yes, ship Friday" };
const choice = {
	title: "Which database?",
	choices: ["SQLite", "PostgreSQL", "MySQL"],
	recommended: "PostgreSQL, for the reporting queries",
	recommendedChoice: 1,
};

test("a question without choices offers 'Yes, as recommended' and a field", () => {
	const form = formQuestion(open, 0);
	assert.equal(form.title, "Q1 · Ship on Friday?");
	assert.equal(form.description, "The release train leaves at 5.");
	assert.deepEqual(form.options, [{ label: "Yes, as recommended", description: "Yes, ship Friday" }]);
	assert.equal(form.allowText, true);
});

test("with choices the recommended one comes first, marked", () => {
	const form = formQuestion(choice, 1);
	assert.equal(form.title, "Q2 · Which database?");
	assert.deepEqual(
		form.options.map((option: any) => option.label),
		["PostgreSQL (Recommended)", "SQLite", "MySQL"],
	);
	// A recommendedChoice out of range falls back to the first choice.
	assert.equal(formQuestion({ ...choice, recommendedChoice: 9 }, 0).options[0].label, "SQLite (Recommended)");
});

test("the form's title carries the round and the progress", () => {
	const form = roundForm("call_1", { round: 2, progress: "3 settled, 2 open", questions: [open, { title: " " }] });
	assert.equal(form.title, "Grilling · round 2 · 3 settled, 2 open");
	assert.equal(form.questions.length, 1, "questions without a title are dropped");
	assert.equal(roundForm("call_2", { questions: [] }), null);
});

test("answers read as decisions the agent can act on", () => {
	assert.equal(
		describeAnswer(open, { id: "0", selected: [0], labels: ["Yes, as recommended"] }),
		"accepted your recommendation (Yes, ship Friday)",
	);
	assert.equal(describeAnswer(choice, { id: "1", selected: [2], labels: ["MySQL"] }), "chose: MySQL");
	assert.equal(
		describeAnswer(choice, { id: "1", selected: [0], labels: ["PostgreSQL (Recommended)"], text: "Postgres 17" }),
		"answered in their own words: Postgres 17",
	);
	assert.equal(describeAnswer(open, undefined), "(no answer)");
});

test("a round's result says what to do next, and stops on cancel", () => {
	const answered = describeRound(2, [open], { status: "answered", answers: [{ id: "0", selected: [0], labels: [] }] });
	assert.match(answered, /^Round 2: the user answered\./);
	assert.match(answered, /next round with grill_round/);
	const cancelled = describeRound(3, [open], { status: "cancelled", answers: [] });
	assert.match(cancelled, /stopped the round/);
	assert.match(cancelled, /Do not continue grilling/);
});
