/**
 * pi-gpui: `/gpui` moves the current terminal session into Pi GPUI, the
 * native desktop app for pi on macOS, Linux and Windows
 * (https://github.com/bytesbay/pi-gpui).
 *
 * pi does not guard a session file against two writers, so the session is
 * handed over only after this pi has exited: a detached process waits for
 * this one to end, then opens `pi-gpui://open?workspace=...&session=...`
 * (with `open` on macOS; on Linux and Windows the link goes straight to the
 * `pi-gpui` binary, which hands it to the running app).
 */
import { execFileSync, spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, win32 } from "node:path";

const INSTALL_HINT =
	process.platform === "darwin"
		? "Install it with `brew install --cask bytesbay/tap/pi-gpui` or from https://github.com/bytesbay/pi-gpui/releases"
		: process.platform === "win32"
			? "Download it from https://github.com/sneccc/pi-gpui/releases, or set PI_GPUI_EXE to pi-gpui.exe"
			: "Install it with `curl -fsSL https://pi-gpui.dev/install.sh | sh`";

/** Where the Windows registry keeps the command that opens pi-gpui:// links. */
const WINDOWS_SCHEME_COMMAND = "HKCU\\Software\\Classes\\pi-gpui\\shell\\open\\command";

export default function (pi: any) {
	pi.registerCommand("gpui", {
		description: "Continue this session in Pi GPUI, the native desktop app",
		handler: async (_args: string, ctx: any) => {
			if (ctx.mode !== "tui") {
				ctx.ui.notify("/gpui works from pi in a terminal.", "warning");
				return;
			}
			// The command that opens a pi-gpui:// link: its last argument is the link.
			let opener: string[];
			if (process.platform === "darwin") {
				const apps = findApps();
				if (apps.length === 0) {
					ctx.ui.notify(`Pi GPUI is not installed. ${INSTALL_HINT}`, "warning");
					return;
				}
				if (!apps.some(handlesLinks)) {
					ctx.ui.notify("This Pi GPUI is too old for /gpui. Update it to 0.1.10 or newer.", "warning");
					return;
				}
				opener = ["open"];
			} else if (process.platform === "linux") {
				const binary = findLinuxBinary();
				if (!binary) {
					ctx.ui.notify(`Pi GPUI is not installed. ${INSTALL_HINT}`, "warning");
					return;
				}
				opener = [binary];
			} else if (process.platform === "win32") {
				const exe = windowsExeCandidates(process.env, registeredWindowsExe()).find((path) => existsSync(path));
				if (!exe) {
					ctx.ui.notify(`Pi GPUI is not installed. ${INSTALL_HINT}`, "warning");
					return;
				}
				opener = [exe];
			} else {
				ctx.ui.notify("Pi GPUI runs on macOS, Linux and Windows.", "warning");
				return;
			}

			const params = new URLSearchParams({ workspace: ctx.cwd });
			const session: string | undefined = ctx.sessionManager.getSessionFile();
			if (session) params.set("session", session);
			const url = `pi-gpui://open?${params}`;

			if (!session) {
				// Nothing on disk to hand over: just open the project there.
				spawn(opener[0], [url], { detached: true, stdio: "ignore" }).unref();
				ctx.ui.notify("Opened this project in Pi GPUI.", "info");
				return;
			}
			if (process.platform === "win32") {
				// No /bin/sh: a detached node does the waiting.
				openAfterExit(process.pid, opener[0], url);
				ctx.ui.notify("Continuing in Pi GPUI.", "info");
				ctx.shutdown();
				return;
			}
			spawn(
				"/bin/sh",
				[
					"-c",
					'while kill -0 "$1" 2>/dev/null; do sleep 0.1; done; exec "$2" "$3"',
					"sh",
					String(process.pid),
					opener[0],
					url,
				],
				{ detached: true, stdio: "ignore" },
			).unref();
			ctx.ui.notify("Continuing in Pi GPUI.", "info");
			ctx.shutdown();
		},
	});
}

/**
 * Where pi-gpui.exe may be, best first: `PI_GPUI_EXE`, PATH, the installer's
 * folder, then whatever the registered pi-gpui:// scheme runs.
 */
export function windowsExeCandidates(env: Record<string, string | undefined>, registered?: string): string[] {
	const candidates: string[] = [];
	if (env.PI_GPUI_EXE) candidates.push(env.PI_GPUI_EXE);
	// Windows environment names are case-insensitive; node keeps the original case.
	const path = Object.entries(env).find(([name]) => name.toUpperCase() === "PATH")?.[1] ?? "";
	for (const directory of path.split(";").filter(Boolean)) {
		candidates.push(win32.join(directory.replace(/^"|"$/g, ""), "pi-gpui.exe"));
	}
	if (env.LOCALAPPDATA) candidates.push(win32.join(env.LOCALAPPDATA, "Programs", "pi-gpui", "pi-gpui.exe"));
	if (registered) candidates.push(registered);
	return candidates;
}

/** The exe in a registered `"C:\...\pi-gpui.exe" "%1"` command, from `reg query` output. */
export function exeFromRegQuery(output: string): string | undefined {
	const line = output.split(/\r?\n/).find((line) => /\sREG_(EXPAND_)?SZ\s/.test(line));
	if (!line) return undefined;
	const command = line.replace(/^.*?\sREG_(EXPAND_)?SZ\s+/, "").trim();
	const exe = command.startsWith('"') ? command.slice(1).split('"')[0] : command.split(/\s+/)[0];
	return exe || undefined;
}

function registeredWindowsExe(): string | undefined {
	try {
		const output = execFileSync("reg", ["query", WINDOWS_SCHEME_COMMAND, "/ve"], {
			encoding: "utf8",
			timeout: 3000,
			windowsHide: true,
			stdio: ["ignore", "pipe", "ignore"],
		});
		return exeFromRegQuery(output);
	} catch {
		return undefined;
	}
}

/**
 * The program a detached node runs: wait until process `argv[1]` has exited,
 * then start `argv[2]` with `argv[3]` (the link) and leave it running.
 */
export const WAIT_THEN_OPEN = `
const [pid, exe, link] = process.argv.slice(1);
const gone = () => { try { process.kill(Number(pid), 0); return false; } catch (error) { return error.code === "ESRCH"; } };
const poll = () => {
  if (!gone()) return setTimeout(poll, 100);
  require("node:child_process").spawn(exe, [link], { detached: true, stdio: "ignore" }).unref();
};
poll();
`;

/** Opens `link` with `exe` once process `pid` has exited, from a detached node. */
export function openAfterExit(pid: number, exe: string, link: string, node = process.execPath) {
	const waiter = spawn(node, ["-e", WAIT_THEN_OPEN, String(pid), exe, link], {
		detached: true,
		stdio: "ignore",
		windowsHide: true,
	});
	waiter.unref();
	return waiter;
}

/** `pi-gpui` on PATH, or where the Linux installer puts it. */
function findLinuxBinary(): string | undefined {
	const candidates = (process.env.PATH ?? "")
		.split(":")
		.filter(Boolean)
		.map((directory) => join(directory, "pi-gpui"));
	candidates.push(join(homedir(), ".local/bin/pi-gpui"), join(homedir(), ".local/pi-gpui.app/bin/pi-gpui"));
	return candidates.find((path) => existsSync(path));
}

function findApps(): string[] {
	const apps = ["/Applications/Pi GPUI.app", join(homedir(), "Applications/Pi GPUI.app")].filter((path) =>
		existsSync(path),
	);
	// Anywhere else Spotlight knows about, e.g. still in Downloads.
	try {
		const found = execFileSync("mdfind", ["kMDItemCFBundleIdentifier == 'dev.pi-gpui.app'"], {
			encoding: "utf8",
			timeout: 3000,
		});
		apps.push(...found.split("\n").filter((path) => path.endsWith(".app")));
	} catch {}
	return apps;
}

/** Pi GPUI 0.1.10 and newer register the pi-gpui:// scheme. */
function handlesLinks(app: string): boolean {
	try {
		return readFileSync(join(app, "Contents/Info.plist"), "utf8").includes("CFBundleURLSchemes");
	} catch {
		return false;
	}
}
