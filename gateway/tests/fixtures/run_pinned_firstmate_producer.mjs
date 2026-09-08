import { chmodSync, readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [root, home] = process.argv.slice(2);
if (!root || !home) throw new Error("usage: run_pinned_firstmate_producer.mjs ROOT HOME");
const moduleUrl = pathToFileURL(`${root}/.pi/extensions/lib/fm-captain-event.ts`).href;
const { installCaptainEventPublisher } = await import(moduleUrl);

const handlers = new Map();
const pi = {
  on(name, handler) {
    const values = handlers.get(name) ?? [];
    values.push(handler);
    handlers.set(name, values);
  },
};
const entries = [];
const context = {
  sessionManager: {
    getSessionId: () => "pinned-runtime-session",
    getEntries: () => entries,
  },
};
installCaptainEventPublisher(pi, {
  fmHome: home,
  fmRoot: root,
  state: `${home}/state`,
  config: `${home}/config`,
  sourceRole: "worker",
  taskId: "soak-runtime-task",
  incarnation: "spawn-runtime-001",
});

for (const handler of handlers.get("session_start") ?? []) {
  await handler({ reason: "startup" }, context);
}

async function turn(number, { stopReason = "toolUse", entryId = `entry-${number}` } = {}) {
  const message = {
    role: "assistant",
    timestamp: 1788900000000 + number,
    stopReason,
    content: [
      { type: "thinking", thinking: "private reasoning must not publish" },
      { type: "text", text: `Captain-visible semantic operation ${String(number).padStart(2, "0")}.` },
      { type: "toolCall", id: `tool-${number}`, name: "read", arguments: { path: "/private" } },
    ],
  };
  entries.push({ id: entryId, type: "message", message });
  for (const handler of handlers.get("turn_end") ?? []) await handler({ message }, context);
}

for (let number = 1; number <= 4; number += 1) await turn(number);

// A producer that becomes invalid after session_start must fail loudly, leave
// its journal unchanged, and permit the same persisted turn to retry after the
// operator repairs activation.
const flag = `${home}/config/captain-event-outbox`;
writeFileSync(flag, "invalid\n", { encoding: "utf8", mode: 0o600 });
let failedAfterStart = false;
try {
  await turn(5, { entryId: "entry-retry-5" });
} catch {
  failedAfterStart = true;
}
if (!failedAfterStart) throw new Error("producer failure after start did not surface");
entries.pop();
writeFileSync(flag, "enabled\n", { encoding: "utf8", mode: 0o600 });
chmodSync(flag, 0o600);
await turn(5, { entryId: "entry-retry-5" });

// The reviewed producer retries one crash after durable pending publication;
// recovery must converge on one event identity rather than append a duplicate.
process.env.FM_CAPTAIN_EVENT_TEST_CRASH = "after-pending";
await turn(6);
delete process.env.FM_CAPTAIN_EVENT_TEST_CRASH;

for (let number = 7; number <= 12; number += 1) {
  await turn(number, { stopReason: number === 12 ? "stop" : "toolUse" });
}

const rows = readFileSync(`${home}/state/captain-events/events.jsonl`, "utf8").trim().split("\n");
if (rows.length !== 12) throw new Error(`expected 12 producer rows, found ${rows.length}`);
process.stdout.write("producer-operations=12 failure-after-start=recovered crash-replay=deduplicated\n");
