// node --no-warnings --test extensions/lease.test.ts   (node 22.18+ runs .ts directly)
//
// The session lease that keeps a terminal pi and Pi GPUI from writing one
// session at the same time. The same code lives in the app's bridge and in
// the pi-gpui package; both copies are tested here.
import assert from "node:assert/strict";
import { type ChildProcess, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import * as bridge from "./pi-gpui-bridge.ts";
import terminalExtension, * as terminal from "../packages/pi-gpui/index.ts";

const root = join(import.meta.dirname, "..");
const folder = mkdtempSync(join(tmpdir(), "pi-gpui-lease-"));
let other: ChildProcess;

before(() => {
	other = spawn(process.execPath, ["-e", "setTimeout(() => {}, 600000)"], { stdio: "ignore" });
});
after(() => {
	other.kill();
	rmSync(folder, { recursive: true, force: true });
});

let counter = 0;
function session(content = '{"type":"session"}\n') {
	const path = join(folder, `s${++counter}.jsonl`);
	if (content !== "") writeFileSync(path, content);
	return path;
}
const leaseOf = (path: string) => JSON.parse(readFileSync(`${path}.lease`, "utf8"));
const foreignLease = (path: string, surface = "Pi GPUI") =>
	writeFileSync(`${path}.lease`, JSON.stringify({ pid: other.pid, surface }));

test("the bridge and the package carry the same lease code", () => {
	const block = (file: string) => {
		const text = readFileSync(join(root, file), "utf8");
		const start = text.indexOf("// --- session lease");
		const end = text.indexOf("// --- end of session lease");
		assert.ok(start >= 0 && end > start, `${file} has no lease block`);
		return text.slice(start, end);
	};
	assert.equal(block("packages/pi-gpui/index.ts"), block("extensions/pi-gpui-bridge.ts"));
});

for (const [name, lease] of [
	["bridge", bridge],
	["package", terminal],
] as const) {
	test(`${name}: holds a session, refreshes, and releases only its own lease`, () => {
		const file = session("");
		const held = lease.holdSession(file, "Pi GPUI", { appPid: 42 });
		assert.equal(existsSync(`${file}.lease`), false, "no lease beside a session file that does not exist yet");
		writeFileSync(file, '{"type":"session"}\n');
		assert.equal(held.refresh(), true);
		const written = leaseOf(file);
		assert.equal(written.pid, process.pid);
		assert.equal(written.surface, "Pi GPUI");
		assert.equal(written.appPid, 42);
		assert.ok(Math.abs(written.startedAt - performance.timeOrigin) < 1000, "startedAt is this process's start");
		assert.equal(lease.leaseHolder(file), undefined, "our own lease is not someone else's");
		held.release();
		assert.equal(existsSync(`${file}.lease`), false);

		const again = lease.holdSession(file, "Pi GPUI");
		foreignLease(file, "a terminal pi");
		again.release();
		assert.equal(leaseOf(file).pid, other.pid, "release must not remove another process's lease");
	});

	test(`${name}: takes the lease as soon as a new session's file appears, without any event`, async () => {
		const file = session("");
		const held = lease.holdSession(file, "a terminal pi");
		try {
			await new Promise((resolve) => setTimeout(resolve, 300));
			assert.equal(existsSync(`${file}.lease`), false);
			writeFileSync(file, '{"type":"session"}\n');
			const end = Date.now() + 2000;
			while (!existsSync(`${file}.lease`) && Date.now() < end) await new Promise((r) => setTimeout(r, 25));
			assert.equal(existsSync(`${file}.lease`), true, "no lease after the file appeared");
			assert.equal(leaseOf(file).pid, process.pid);
		} finally {
			held.release();
		}
	});

	test(`${name}: a live holder counts; dead, stale and missing ones do not`, () => {
		const file = session();
		assert.equal(lease.leaseHolder(file), undefined);
		foreignLease(file, "a terminal pi");
		assert.equal(lease.leaseHolder(file), "a terminal pi");
		assert.equal(lease.leaseHolder(file, Date.now() + lease.LEASE_TTL_MS + 60_000), undefined, "stale");
		writeFileSync(`${file}.lease`, JSON.stringify({ pid: 999_999_99, surface: "a terminal pi" }));
		assert.equal(lease.leaseHolder(file), undefined, "dead process");
		writeFileSync(`${file}.lease`, "not json");
		assert.equal(lease.leaseHolder(file), undefined);
	});

	test(`${name}: never overwrites a live holder's lease`, () => {
		const file = session();
		foreignLease(file);
		const held = lease.holdSession(file, "a terminal pi");
		assert.equal(held.refresh(), false);
		assert.equal(leaseOf(file).pid, other.pid);
		held.release();
		assert.equal(leaseOf(file).pid, other.pid);
	});
}

test("the app's pi takes a session over even when its lease names a live process", () => {
	// The app checked the lease (with start times) before opening the session;
	// the pid may already belong to an unrelated process.
	const file = session();
	foreignLease(file, "a terminal pi");
	const held = bridge.holdSession(file, "Pi GPUI", { appPid: 1 }, true);
	assert.equal(leaseOf(file).pid, process.pid);
	assert.equal(leaseOf(file).surface, "Pi GPUI");
	held.release();
	assert.equal(existsSync(`${file}.lease`), false);
});

test("the bridge takes over its session when the app opens it", () => {
	const handlers: Record<string, (event: any, ctx: any) => any> = {};
	bridge.default({ on: (name: string, handler: any) => (handlers[name] = handler), registerCommand() {} });
	const file = session();
	foreignLease(file, "a terminal pi");
	const ctx = { sessionManager: { getSessionFile: () => file }, ui: { setStatus() {} }, hasUI: true };
	handlers.session_start({ reason: "startup" }, ctx);
	assert.equal(leaseOf(file).surface, "Pi GPUI");
	handlers.session_shutdown({ reason: "quit" }, ctx);
	assert.equal(existsSync(`${file}.lease`), false);
});

test("a lease older than the TTL is taken over", () => {
	const file = session();
	foreignLease(file);
	const old = (Date.now() - terminal.LEASE_TTL_MS - 60_000) / 1000;
	utimesSync(`${file}.lease`, old, old);
	const held = terminal.holdSession(file, "a terminal pi");
	assert.equal(leaseOf(file).pid, process.pid);
	held.release();
});

/** Loads the terminal extension into a fake pi and returns its event handlers. */
function loadTerminal() {
	const handlers: Record<string, (event: any, ctx: any) => any> = {};
	terminalExtension({ on: (name: string, handler: any) => (handlers[name] = handler), registerCommand() {} });
	return handlers;
}
function context(file: string | undefined, mode = "tui", confirm = false) {
	const notes: string[] = [];
	const ctx = {
		mode,
		hasUI: true,
		sessionManager: { getSessionFile: () => file },
		ui: { notify: (text: string) => notes.push(text), confirm: async () => confirm },
	};
	return { ctx, notes };
}

test("terminal pi: warns when Pi GPUI has the session, and does not take its lease", () => {
	const on = loadTerminal();
	const file = session();
	foreignLease(file, "Pi GPUI");
	const { ctx, notes } = context(file);
	on.session_start({ reason: "startup" }, ctx);
	assert.deepEqual(notes, [terminal.sharedSessionWarning("Pi GPUI")]);
	assert.equal(leaseOf(file).pid, other.pid);
	on.session_shutdown({ reason: "quit" }, ctx);
	assert.equal(leaseOf(file).pid, other.pid);
});

test("terminal pi: holds a free session quietly and lets go on shutdown", () => {
	const on = loadTerminal();
	const file = session();
	const { ctx, notes } = context(file);
	on.session_start({ reason: "startup" }, ctx);
	assert.deepEqual(notes, []);
	assert.equal(leaseOf(file).surface, "a terminal pi");
	on.session_shutdown({ reason: "quit" }, ctx);
	assert.equal(existsSync(`${file}.lease`), false);
});

test("terminal pi: /resume into a held session asks first", async () => {
	const on = loadTerminal();
	const held = session();
	foreignLease(held, "Pi GPUI");
	const no = context(session(), "tui", false);
	assert.deepEqual(await on.session_before_switch({ reason: "resume", targetSessionFile: held }, no.ctx), {
		cancel: true,
	});
	const yes = context(session(), "tui", true);
	assert.equal(await on.session_before_switch({ reason: "resume", targetSessionFile: held }, yes.ctx), undefined);
	const free = session();
	assert.equal(await on.session_before_switch({ reason: "resume", targetSessionFile: free }, no.ctx), undefined);
});

test("inside Pi GPUI (rpc mode) the package leaves the lease to the app's bridge", () => {
	const on = loadTerminal();
	const file = session();
	on.session_start({ reason: "startup" }, context(file, "rpc").ctx);
	assert.equal(existsSync(`${file}.lease`), false);
});
