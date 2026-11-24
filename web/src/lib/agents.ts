import type { Agent } from "../api";
import type { Area } from "./areas";
import { tool } from "./tools";

const FALLBACK: Area[] = ["notes", "activity", "settings", "inbox", "tasks"];

/** Each agent's colour: Gabe blue, the brief writer yellow, a specialist the colour of the work it mostly does. */
export function agentHue(a: Agent, all: Agent[]): Area {
  if (a.kind === "chief") return "ask";
  if (a.kind === "briefing") return "today";
  const counts: Record<string, number> = {};
  for (const t of a.tools) {
    const area = tool(t).area;
    if (area !== "neutral") counts[area] = (counts[area] ?? 0) + 1;
  }
  const best = Object.entries(counts).sort((x, y) => y[1] - x[1])[0]?.[0] as Area | undefined;
  // Earlier agents keep their colour when new ones are added: only look at those listed before this one.
  const before = all.slice(0, all.indexOf(a)).filter((o) => o.kind === "member");
  const taken = new Set(before.map((o) => agentHue(o, all)));
  if (best && !taken.has(best)) return best;
  return FALLBACK.find((h) => !taken.has(h)) ?? "notes";
}

export function agentLabel(a: Agent) {
  return a.name;
}

export function nowText(a: Agent): string {
  if (a.status === "paused") return a.held ? "Paused (its manager is paused)" : "Paused";
  if (a.status === "waiting") return "Waiting for your OK";
  if (!a.now) return a.status === "working" ? "Working…" : "Idle";
  if (a.now.kind === "tool") return tool(a.now.tool).past(a.now.input);
  return a.now.text;
}
