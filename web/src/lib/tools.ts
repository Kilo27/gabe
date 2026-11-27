// Plain-English names for Gabe's actions, so nobody has to read tool names.
import type { Area } from "./areas";

type ToolInfo = { label: string; area: Area | "neutral"; past: (i: any) => string; ask: string };

const q = (s: unknown) => (s ? `“${String(s)}”` : "");
const list = (v: unknown) => (Array.isArray(v) ? v.join(", ") : String(v ?? ""));

const and = (xs: string[], word = "and") => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ${word} ${xs[xs.length - 1]}`);
const AGE: Record<string, [string, string]> = { h: ["hour", "hours"], d: ["day", "days"], m: ["month", "months"], y: ["year", "years"] };
const age = (n: string, u: string) => (n === "1" ? `the last ${AGE[u][0]}` : `the last ${n} ${AGE[u][1]}`);

/** A Gmail search, said in words: "newer_than:1d -category:promotions" → "email from the last day, skipping promotions". */
export function emailSearchWords(query: unknown): string {
  let rest = String(query ?? "").trim();
  if (!rest) return "email";
  const kinds: string[] = [], skip: string[] = [], from: string[] = [], to: string[] = [], about: string[] = [], extra: string[] = [];
  let when = "";
  const take = (re: RegExp, fn: (...m: string[]) => void) => { rest = rest.replace(re, (...m) => { fn(...(m as string[])); return " "; }); };
  // (is:unread OR is:important)
  take(/\(\s*((?:-?is:\w+\s+OR\s+)+-?is:\w+)\s*\)/gi, (_, g) => kinds.push(and(g.split(/\s+OR\s+/i).map((x) => x.replace(/^-?is:/i, "")), "or")));
  take(/(^|\s)newer_than:(\d+)([hdmy])/gi, (_, __, n, u) => { when = `from ${age(n, u.toLowerCase())}`; });
  take(/(^|\s)older_than:(\d+)([hdmy])/gi, (_, __, n, u) => { when = `older than ${n} ${AGE[u.toLowerCase()][n === "1" ? 0 : 1]}`; });
  take(/(^|\s)-category:(\w+)/gi, (_, __, c) => skip.push(c.toLowerCase()));
  take(/(^|\s)category:(\w+)/gi, (_, __, c) => kinds.push(c.toLowerCase()));
  take(/(^|\s)-is:(\w+)/gi, (_, __, c) => kinds.push(`not ${c.toLowerCase()}`));
  take(/(^|\s)is:(\w+)/gi, (_, __, c) => kinds.push(c.toLowerCase()));
  take(/(^|\s)from:("[^"]+"|\S+)/gi, (_, __, v) => from.push(v.replace(/"/g, "")));
  take(/(^|\s)to:("[^"]+"|\S+)/gi, (_, __, v) => to.push(v.replace(/"/g, "")));
  take(/(^|\s)subject:("[^"]+"|\S+)/gi, (_, __, v) => about.push(v.replace(/"/g, "")));
  take(/(^|\s)has:attachment\b/gi, () => extra.push("with attachments"));
  take(/(^|\s)(?:in|label):(\S+)/gi, (_, __, v) => { if (v.toLowerCase() !== "inbox") extra.push(`labelled ${v}`); });
  const words = rest.replace(/\s+/g, " ").trim();
  if (words) about.push(words.replace(/"/g, ""));
  return [
    [and(kinds), "email"].filter(Boolean).join(" "),
    from.length && `from ${and(from, "or")}`,
    to.length && `to ${and(to, "or")}`,
    when,
    ...extra,
    about.length && `about ${about.map((x) => q(x)).join(" ")}`,
    skip.length && `, skipping ${and(skip)}`,
  ].filter(Boolean).join(" ").replace(/ ,/g, ",");
}

/** Any action input value, readable. */
export function showValue(toolName: string, key: string, v: unknown): string {
  if (key === "query" && toolName === "search_email") return emailSearchWords(v);
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v == null || v === "") return "–";
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? showValue(toolName, "", x) : String(x))).join(", ");
  if (typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${humanize(k)}: ${showValue(toolName, k, x)}`).join(" · ");
  return String(v);
}

export const TOOLS: Record<string, ToolInfo> = {
  get_current_time: { label: "Check the date and time", area: "neutral", past: () => "Checked the date and time", ask: "check the date and time" },
  remember: { label: "Save a note", area: "notes", past: (i) => `Saved a note about ${i?.topic ?? "something"}`, ask: "save a note" },
  recall: { label: "Look up notes", area: "notes", past: (i) => (i?.query ? `Looked up notes on ${q(i.query)}` : "Looked through recent notes"), ask: "look up notes" },
  forget: { label: "Delete a note", area: "notes", past: () => "Deleted a note", ask: "delete a note" },
  edit_note: { label: "Edit a note", area: "notes", past: (i) => `Edited the note on ${i?.topic ?? "a topic"}`, ask: "edit a note" },
  create_task: { label: "Add a task", area: "tasks", past: (i) => `Added the task ${q(i?.title)}${i?.owner ? ` for ${i.owner}` : ""}`, ask: "add a task" },
  list_tasks: { label: "Check the task list", area: "tasks", past: (i) => (i?.owner ? `Checked ${i.owner}'s tasks` : "Checked the task list"), ask: "check the task list" },
  update_task: { label: "Update a task", area: "tasks", past: (i) => `Updated a task${i?.status ? ` to “${statusWord(i.status)}”` : ""}`, ask: "update a task" },
  search_email: { label: "Search email", area: "inbox", past: (i) => `Searched ${emailSearchWords(i?.query)}`, ask: "search email" },
  read_email: { label: "Read an email", area: "inbox", past: () => "Read an email", ask: "read an email" },
  draft_email: { label: "Write an email draft", area: "inbox", past: (i) => `Saved a draft to ${list(i?.to)}${i?.subject ? `: ${q(i.subject)}` : ""}`, ask: "save an email draft" },
  send_email: { label: "Send an email", area: "inbox", past: (i) => `Sent an email to ${list(i?.to)}${i?.subject ? `: ${q(i.subject)}` : ""}`, ask: "send an email" },
  mark_email_read: { label: "Mark an email as read", area: "inbox", past: () => "Marked an email as read", ask: "mark an email as read" },
  list_events: { label: "Check the calendar", area: "today", past: (i) => (i?.query ? `Looked in the calendar for ${q(i.query)}` : "Checked the calendar"), ask: "check the calendar" },
  find_free_time: { label: "Find free time", area: "today", past: (i) => `Looked for ${i?.duration_minutes ?? ""} free minutes${i?.attendees?.length ? ` with ${list(i.attendees)}` : ""}`, ask: "find free time" },
  block_time: { label: "Block time in the calendar", area: "today", past: (i) => `Blocked time for ${q(i?.title)}`, ask: "block time in the calendar" },
  create_event: { label: "Book a meeting", area: "today", past: (i) => `Booked ${q(i?.title)}${i?.attendees?.length ? ` with ${list(i.attendees)}` : ""}`, ask: "book a meeting" },
  update_event: { label: "Change a meeting", area: "today", past: (i) => `Changed ${i?.title ? q(i.title) : "a meeting"}`, ask: "change a meeting" },
  cancel_event: { label: "Cancel a meeting", area: "today", past: () => "Cancelled a meeting", ask: "cancel a meeting" },
  respond_to_invite: { label: "Answer an invitation", area: "today", past: (i) => (i?.response === "declined" ? "Declined an invitation" : i?.response === "tentative" ? "Said maybe to an invitation" : "Accepted an invitation"), ask: "answer a meeting invitation" },
  schedule_job: { label: "Set up a recurring job", area: "tasks", past: (i) => `Set up ${q(i?.name)} to repeat`, ask: "set up a recurring job" },
  list_schedules: { label: "Check recurring jobs", area: "tasks", past: () => "Checked recurring jobs", ask: "check recurring jobs" },
  update_schedule: { label: "Change a recurring job", area: "tasks", past: (i) => (i?.paused === true ? "Paused a recurring job" : i?.paused === false ? "Resumed a recurring job" : "Changed a recurring job"), ask: "change a recurring job" },
  cancel_schedule: { label: "Stop a recurring job", area: "tasks", past: () => "Stopped a recurring job", ask: "stop a recurring job" },
  delegate: { label: "Hand work to a team member", area: "ask", past: (i) => `Handed work to ${humanize(String(i?.specialist ?? "a team member"))}`, ask: "hand work to a team member" },
  add_team_member: { label: "Add a team member", area: "ask", past: (i) => `Added ${i?.name ?? "a new bot"} to the team`, ask: "add a new team member" },
  change_gabe: { label: "Change Gabe's setup", area: "settings", past: () => "Changed Gabe's setup", ask: "change its own setup" },
  open_claude_code: { label: "Open Claude Code", area: "settings", past: () => "Opened Claude Code", ask: "open Claude Code" },
  edit_settings: { label: "Change settings", area: "settings", past: (i) => `Changed settings${i?.section ? ` (${i.section})` : ""}`, ask: "change settings" },
  edit_playbook: { label: "Edit a playbook", area: "settings", past: (i) => `Edited the ${i?.name ?? ""} playbook`, ask: "edit a playbook" },
  search_faq: { label: "Search the FAQ", area: "settings", past: (i) => `Searched the FAQ${i?.query ? ` for ${q(i.query)}` : ""}`, ask: "search the FAQ" },
  reply_on_slack: { label: "Answer a customer on Slack", area: "ask", past: (i) => `Answered a question in ${i?.channel ?? "Slack"}`, ask: "answer a customer on Slack" },
  upload_faq: { label: "Upload an FAQ document", area: "settings", past: (i) => `Uploaded ${i?.name ?? "an FAQ document"}`, ask: "upload an FAQ document" },
  delete_faq: { label: "Remove an FAQ document", area: "settings", past: (i) => `Removed ${i?.name ?? "an FAQ document"}`, ask: "remove an FAQ document" },
  delete_playbook: { label: "Delete a playbook", area: "settings", past: (i) => `Deleted the ${i?.name ?? ""} playbook`, ask: "delete a playbook" },
};

export function tool(name: string): ToolInfo {
  return TOOLS[name] ?? { label: humanize(name), area: "neutral", past: () => humanize(name), ask: humanize(name).toLowerCase() };
}

export function humanize(s: string) {
  const t = s.replace(/[_.-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export const OUTCOMES: Record<string, { label: string; area: Area | "danger" | "neutral"; help: string }> = {
  ok: { label: "Done", area: "tasks", help: "This worked." },
  declined_by_user: { label: "Not approved", area: "approvals", help: "A person said no, so Gabe didn't do it." },
  denied_by_policy: { label: "Blocked by rules", area: "danger", help: "Your company's rules don't allow this, so Gabe didn't do it." },
  error: { label: "Failed", area: "danger", help: "Gabe tried but something went wrong." },
  invalid_input: { label: "Failed", area: "danger", help: "Gabe asked for this in the wrong way and it didn't run." },
  unknown_tool: { label: "Failed", area: "danger", help: "Gabe asked for something it can't do." },
};
export const outcome = (o: string) => OUTCOMES[o] ?? { label: humanize(o), area: "neutral" as const, help: "" };

export const RISK: Record<string, { label: string; help: string }> = {
  read: { label: "Looks things up", help: "Reads information. Changes nothing." },
  write: { label: "Changes Gabe's own records", help: "Changes tasks, notes or drafts inside Gabe." },
  external: { label: "Reaches outside the company", help: "Does something other people will see, like sending an email." },
};

export const DECISION_WORDS: Record<string, { label: string; help: string; area: Area | "danger" }> = {
  allow: { label: "Just do it", help: "Gabe does this on its own.", area: "tasks" },
  ask: { label: "Ask first", help: "Gabe waits for a person to approve.", area: "approvals" },
  deny: { label: "Never", help: "Gabe is never allowed to do this.", area: "danger" },
};

export function agentName(agent: string, assistant = "Gabe") {
  if (!agent) return assistant;
  if (agent === "You (web app)") return "A person (web app)";
  if (agent === "briefing") return `${assistant} (sending the brief)`;
  if (agent.endsWith("(briefing)")) return `${assistant} (writing the brief)`;
  if (agent === assistant) return assistant;
  return `${humanize(agent)} specialist`;
}

export const STATUS: Record<string, { label: string; area: Area | "danger" | "neutral" }> = {
  open: { label: "To do", area: "neutral" },
  in_progress: { label: "In progress", area: "ask" },
  blocked: { label: "Blocked", area: "danger" },
  done: { label: "Done", area: "tasks" },
  cancelled: { label: "Cancelled", area: "neutral" },
};
export const statusWord = (s: string) => STATUS[s]?.label ?? humanize(s);
