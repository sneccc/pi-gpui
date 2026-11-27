/**
 * Pi GPUI bridge.
 *
 * Pi GPUI loads this file with `pi --mode rpc --extension <path>` for its
 * own pi processes only; nothing is installed. It fills two gaps in pi's RPC
 * protocol:
 *
 * 1. Moving within the session tree: the app sends
 *    `/pi-gpui-navigate <entryId> [summarize]` as a prompt and reloads the
 *    transcript when pi answers "handled".
 * 2. Usage details for the context popover: the last turn's tokens, the
 *    prompt cache lifetime, whether the provider bills a subscription, and
 *    plan limits from provider response headers. They are published as JSON
 *    through `setStatus("pi-gpui:usage", ...)`, which RPC forwards.
 * 3. A lease on the open session (see "session lease" below), so a terminal
 *    pi with the pi-gpui package can tell that Pi GPUI has it open. The app
 *    sets PI_GPUI_APP_PID, so it can tell its own pi processes' leases apart.
 */
import * as fs from "node:fs";

const STATUS_KEY = "pi-gpui:usage";

type PlanLimit = { windowMinutes: number; usedPercent: number; resetsAt?: number };

export default function (pi: any) {
	let limits: { provider: string; limits: PlanLimit[] } | undefined;

	pi.registerCommand("pi-gpui-navigate", {
		description: "Internal: used by Pi GPUI to move within the session tree",
		handler: async (args: string, ctx: any) => {
			const [targetId, mode] = args.trim().split(/\s+/);
			if (!targetId) return;
			await ctx.waitForIdle?.();
			await ctx.navigateTree(targetId, { summarize: mode === "summarize" });
		},
	});

	const publish = (ctx: any) => {
		try {
			const model = ctx.model;
			if (!model || !ctx.hasUI) return;
			const branch = ctx.sessionManager.getBranch();
			const last = latestAssistantUsage(branch);
			let touchedAt: number | undefined;
			if (last && last.provider === model.provider && last.model === model.id) {
				touchedAt = last.at;
				for (const entry of branch) {
					if (entry.type !== "usage" || entry.kind !== "cache_warm" || entry.provider !== model.provider) continue;
					const at = Date.parse(entry.timestamp);
					if (Number.isFinite(at) && at > touchedAt) touchedAt = at;
				}
			}
			const retention = process.env.PI_CACHE_RETENTION === "long" ? "long" : "short";
			let subscription = model.provider === "kimi-coding";
			try {
				subscription ||= Boolean(ctx.modelRegistry?.isUsingOAuth?.(model));
			} catch {}
			ctx.ui.setStatus(
				STATUS_KEY,
				JSON.stringify({
					subscription,
					lastTurn: last?.counts,
					cacheSeconds: model.promptCache?.[retention],
					cacheTouchedAt: touchedAt,
					limits: limits && limits.provider === model.provider ? limits.limits : undefined,
				}),
			);
		} catch {}
	};

	let lease: ReturnType<typeof holdSession> | undefined;
	const appPid = Number(process.env.PI_GPUI_APP_PID) || undefined;

	pi.on("session_start", (_event: unknown, ctx: any) => {
		publish(ctx);
		lease?.release();
		const session: string | undefined = ctx.sessionManager.getSessionFile?.();
		// The app opened this session after checking its lease: take it over.
		lease = session ? holdSession(session, "Pi GPUI", { appPid }, true) : undefined;
	});
	pi.on("session_shutdown", () => {
		lease?.release();
		lease = undefined;
	});
	pi.on("agent_start", () => lease?.refresh());
	pi.on("model_select", (_event: unknown, ctx: any) => publish(ctx));
	pi.on("agent_settled", (_event: unknown, ctx: any) => {
		publish(ctx);
		lease?.refresh();
	});
	pi.on("after_provider_response", (event: any, ctx: any) => {
		const provider = ctx.model?.provider;
		if (!provider || event.status < 200 || event.status >= 300) return;
		const parsed = parsePlanLimits(event.headers ?? {});
		if (parsed.length === 0) return;
		limits = { provider, limits: parsed };
		publish(ctx);
	});
}

function latestAssistantUsage(branch: any[]) {
	for (let index = branch.length - 1; index >= 0; index -= 1) {
		const entry = branch[index];
		if (entry?.type !== "message" || entry.message?.role !== "assistant") continue;
		const message = entry.message;
		if (message.stopReason === "aborted" || message.stopReason === "error") continue;
		const { input = 0, output = 0, cacheRead = 0, cacheWrite = 0 } = message.usage ?? {};
		if (input + output + cacheRead + cacheWrite === 0) continue;
		return {
			counts: { input, output, cacheRead, cacheWrite },
			provider: message.provider,
			model: message.model,
			at: message.timestamp,
		};
	}
	return undefined;
}

/** Subscription limits: ChatGPT (Codex) and Claude unified rate-limit headers. */
function parsePlanLimits(raw: Record<string, string>): PlanLimit[] {
	const headers = new Map(Object.entries(raw).map(([name, value]) => [name.toLowerCase(), value]));
	const number = (name: string) => {
		const value = headers.get(name);
		if (value === undefined || value.trim() === "") return undefined;
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : undefined;
	};
	const limits: PlanLimit[] = [];
	for (const slot of ["primary", "secondary"]) {
		const windowMinutes = number(`x-codex-${slot}-window-minutes`);
		const usedPercent = number(`x-codex-${slot}-used-percent`);
		if (!windowMinutes || usedPercent === undefined) continue;
		const resetAt = number(`x-codex-${slot}-reset-at`);
		const resetAfter = number(`x-codex-${slot}-reset-after-seconds`);
		const resetsAt = resetAt ? resetAt * 1000 : resetAfter ? Date.now() + resetAfter * 1000 : undefined;
		limits.push({ windowMinutes, usedPercent, resetsAt });
	}
	for (const [slot, windowMinutes] of [["5h", 300], ["7d", 10080]] as const) {
		const utilization = number(`anthropic-ratelimit-unified-${slot}-utilization`);
		if (utilization === undefined) continue;
		const reset = number(`anthropic-ratelimit-unified-${slot}-reset`);
		limits.push({ windowMinutes, usedPercent: utilization * 100, resetsAt: reset ? reset * 1000 : undefined });
	}
	return limits;
}

// --- session lease ---------------------------------------------------------
// Keep this block identical in extensions/pi-gpui-bridge.ts and
// packages/pi-gpui/index.ts (extensions/lease.test.ts checks it).
//
// pi does not stop two processes from writing one session file, so whoever
// has a session open says so in `<session>.jsonl.lease` (pi-gui's
// convention): {pid, surface, startedAt, updatedAt[, appPid]}. A lease counts
// while its process lives and it was refreshed in the last five minutes;
// `startedAt` (the process start, ms) tells a reused pid apart.

/** pi-gui treats a lease as stale after five minutes; refresh well within that. */
export const LEASE_TTL_MS = 5 * 60_000;
const LEASE_REFRESH_MS = 60_000;

export type Lease = { pid: number; surface?: string; startedAt?: number; updatedAt?: number; appPid?: number };

export function readLease(session: string): Lease | undefined {
	try {
		const lease = JSON.parse(fs.readFileSync(`${session}.lease`, "utf8"));
		return typeof lease?.pid === "number" ? lease : undefined;
	} catch {
		return undefined;
	}
}

/** Who else, a live process, holds `session`: its surface ("Pi GPUI", "a terminal pi"). */
export function leaseHolder(session: string, now = Date.now()): string | undefined {
	let modified: number;
	try {
		modified = fs.statSync(`${session}.lease`).mtimeMs;
	} catch {
		return undefined;
	}
	if (now - modified > LEASE_TTL_MS) return undefined;
	const lease = readLease(session);
	if (!lease || lease.pid === process.pid || !processAlive(lease.pid)) return undefined;
	return lease.surface || "another app";
}

function processAlive(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch (error: any) {
		return error?.code === "EPERM";
	}
}

/**
 * Holds `session` for this process: writes the lease (once the session file
 * exists, and never over another live holder's lease), rewrites it every
 * minute and on `refresh()`, and removes it on `release()` or exit.
 * `takeOver`: write even over a lease that looks live. For Pi GPUI's own pi:
 * the app checked the lease before opening the session, with process start
 * times, which node cannot read; a pid Windows already reused for another
 * process would otherwise look like a live holder.
 */
export function holdSession(session: string, surface: string, extra: Partial<Lease> = {}, takeOver = false) {
	const path = `${session}.lease`;
	const refresh = (): boolean => {
		// Cheap when called often (every saved message): our own fresh lease stays.
		const current = readLease(session);
		if (current?.pid === process.pid && Date.now() - (current.updatedAt ?? 0) < LEASE_REFRESH_MS / 2) return true;
		if (!fs.existsSync(session) || (!takeOver && leaseHolder(session))) return false;
		const lease: Lease = {
			pid: process.pid,
			surface,
			startedAt: Math.round(performance.timeOrigin),
			updatedAt: Date.now(),
			...extra,
		};
		const staged = `${path}.${process.pid}.tmp`;
		try {
			fs.writeFileSync(staged, JSON.stringify(lease));
			fs.renameSync(staged, path);
			return true;
		} catch {
			try {
				fs.unlinkSync(staged);
			} catch {}
			return false;
		}
	};
	const timer = setInterval(refresh, LEASE_REFRESH_MS);
	timer.unref?.();
	// A new session's file appears during its first run, at a moment no event
	// marks reliably: look for it until it is there, then take the lease at once.
	const appear = setInterval(() => {
		if (!fs.existsSync(session)) return;
		clearInterval(appear);
		refresh();
	}, 250);
	appear.unref?.();
	const release = () => {
		clearInterval(timer);
		clearInterval(appear);
		process.off("exit", release);
		if (readLease(session)?.pid !== process.pid) return;
		try {
			fs.unlinkSync(path);
		} catch {}
	};
	process.on("exit", release);
	refresh();
	return { refresh, release };
}
// --- end of session lease ---------------------------------------------------
