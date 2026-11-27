/**
 * A pi extension for tests: registers the faux model `faux/faux-1`, which
 * answers every prompt with "SLOW_REPLY_DONE <n>" after PI_SLOW_MODEL_MS
 * milliseconds (default 6000). Costs nothing, needs no login.
 *
 *     pi --offline -e script/debug/slow-model.ts --provider faux --model faux-1 --thinking off
 */
import { fauxAssistantMessage, fauxProvider } from "@earendil-works/pi-ai";

export default function (pi: any) {
	const delay = Number(process.env.PI_SLOW_MODEL_MS ?? 6000);
	const faux = fauxProvider();
	let count = 0;
	const reply = async () => {
		await new Promise((resolve) => setTimeout(resolve, delay));
		count += 1;
		// Keep a reply ready for the next prompt too.
		faux.appendResponses([reply]);
		return fauxAssistantMessage(`SLOW_REPLY_DONE ${count}`);
	};
	faux.setResponses([reply]);
	pi.registerProvider(faux.provider);
}
