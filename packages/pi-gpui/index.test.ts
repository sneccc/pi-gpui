// node --no-warnings --test packages/pi-gpui/index.test.ts   (node 22.18+ runs .ts directly)
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { exeFromRegQuery, openAfterExit, windowsExeCandidates } from "./index.ts";

test("windows candidates: PI_GPUI_EXE first, then PATH, the install folder, the registered exe", () => {
	const candidates = windowsExeCandidates(
		{
			PI_GPUI_EXE: "D:\\mine\\pi-gpui.exe",
			Path: 'C:\\bin;;"C:\\Program Files\\Pi GPUI"',
			LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local",
		},
		"E:\\registered\\pi-gpui.exe",
	);
	assert.deepEqual(candidates, [
		"D:\\mine\\pi-gpui.exe",
		"C:\\bin\\pi-gpui.exe",
		"C:\\Program Files\\Pi GPUI\\pi-gpui.exe",
		"C:\\Users\\me\\AppData\\Local\\Programs\\pi-gpui\\pi-gpui.exe",
		"E:\\registered\\pi-gpui.exe",
	]);
});

test("windows candidates: nothing set, nothing found", () => {
	assert.deepEqual(windowsExeCandidates({}), []);
});

test("reads the exe from reg query output", () => {
	const output = [
		"",
		"HKEY_CURRENT_USER\\Software\\Classes\\pi-gpui\\shell\\open\\command",
		'    (Default)    REG_SZ    "C:\\Program Files\\pi gpui\\pi-gpui.exe" "%1"',
		"",
	].join("\r\n");
	assert.equal(exeFromRegQuery(output), "C:\\Program Files\\pi gpui\\pi-gpui.exe");
	assert.equal(exeFromRegQuery('    (Default)    REG_SZ    C:\\apps\\pi-gpui.exe "%1"'), "C:\\apps\\pi-gpui.exe");
	assert.equal(exeFromRegQuery("ERROR: The system was unable to find the specified registry key or value."), undefined);
	assert.equal(exeFromRegQuery('    (Default)    REG_SZ    "" "%1"'), undefined);
});

test("openAfterExit waits for the process to end, then opens the link", async () => {
	const folder = mkdtempSync(join(tmpdir(), "pi-gpui-wait-"));
	let pi: ReturnType<typeof spawn> | undefined;
	try {
		// The waiter runs `exe link`. Stand-ins: exe = node, "link" = a script
		// (in a folder with a space) that leaves a marker when it runs.
		const marker = join(folder, "opened.txt");
		const app = join(folder, "the app.js");
		writeFileSync(app, `require("node:fs").writeFileSync(${JSON.stringify(marker)}, "opened");`);
		// The "pi" that must exit first.
		pi = spawn(process.execPath, ["-e", "setTimeout(() => {}, 60000)"], { stdio: "ignore" });
		openAfterExit(pi.pid!, process.execPath, app);
		await new Promise((resolve) => setTimeout(resolve, 800));
		assert.equal(existsSync(marker), false, "opened while pi was still running");
		pi.kill();
		const end = Date.now() + 10000;
		while (!existsSync(marker) && Date.now() < end) await new Promise((resolve) => setTimeout(resolve, 100));
		assert.equal(existsSync(marker) && readFileSync(marker, "utf8"), "opened", "never opened after pi exited");
	} finally {
		pi?.kill();
		rmSync(folder, { recursive: true, force: true });
	}
});
