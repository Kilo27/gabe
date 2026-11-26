// Small fetch wrapper. Errors carry the server's plain-English message.
export class ApiError extends Error {}

// The desktop app opens the page with a per-launch token in the URL hash; every API call sends it.
const TOKEN = (() => {
  const m = location.hash.match(/(?:^#|&)t=([^&]+)/);
  if (m) {
    try { sessionStorage.setItem("gabe-token", m[1]); } catch { /* ignore */ }
    history.replaceState(null, "", location.pathname + location.search);
    return m[1];
  }
  try { return sessionStorage.getItem("gabe-token") ?? ""; } catch { return ""; }
})();

type NativeApi = {
  save_text(filename: string, content: string): Promise<boolean>;
  open_url(url: string): Promise<void>;
};
/** Native features in the desktop app; undefined in a browser. */
export const native = (): NativeApi | undefined => (window as any).pywebview?.api;

export async function getText(url: string): Promise<string> {
  const res = await fetch(url, { headers: TOKEN ? { "X-Gabe-Token": TOKEN } : undefined });
  if (!res.ok) throw new ApiError("Couldn't export. Try again.");
  return res.text();
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(TOKEN ? { "X-Gabe-Token": TOKEN } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Can't reach Gabe. Try restarting the app.");
  }
  if (!res.ok) {
    let msg = `Something went wrong (error ${res.status}).`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => call<T>("GET", url),
  post: <T>(url: string, body: unknown = {}) => call<T>("POST", url, body),
  put: <T>(url: string, body: unknown) => call<T>("PUT", url, body),
  patch: <T>(url: string, body: unknown) => call<T>("PATCH", url, body),
  del: <T>(url: string) => call<T>("DELETE", url),
};

// ---- Types -------------------------------------------------------------------

export type Job = { name: string; status: "idle" | "running" | "done" | "error"; error?: string | null; result?: any; started?: string; finished?: string | null };

export type Overview = {
  assistant: string;
  company: string;
  timezone: string;
  today: string;
  approvals_pending: number;
  tasks: { active: number; overdue: number; due_today: number; due_week: number; blocked: number };
  notes: number;
  latest_brief: { date: string; title: string; text: string } | null;
  next_brief: string | null;
  brief_job: Job | null;
  email: EmailStatus;
};

export type EmailStatus = { state: "off" | "practice" | "missing_file" | "disconnected" | "connected" | "error"; message: string; address?: string; backend?: string; mailboxes?: { address: string; connected: boolean }[] };

export type Task = {
  id: string; title: string; owner: string; due: string | null; notes: string; project: string;
  status: TaskStatus; created: string; source?: string;
  handled_by?: { agent: string; agent_id: string; session_id: string; at: string };
};
export type Schedule = {
  id: string; name: string; instruction: string; when: string; every_minutes: number | null; at: string | null;
  days: string[]; between: string | null; paused: boolean; next_run: string; last_run: string | null; runs: number; session_id: string | null;
};
export type TaskStatus = "open" | "in_progress" | "blocked" | "done" | "cancelled";

export type Note = { id: string; topic: string; note: string; tags: string[]; created: string; edited?: string };

export type Approval = {
  kind?: "approval" | "question"; answer?: string;
  id: string; status: "pending" | "approved" | "rejected" | "expired" | "answered"; created: string; decided: string | null;
  session_id: string; session_title: string; tool: string; risk: string; integration: string; description: string;
  args: Record<string, any>; original_args?: Record<string, any>; reason: string; note: string; edited: boolean;
  decided_by?: string; live?: boolean; agent?: string;
};

export type ActivityItem =
  | { kind: "tool"; agent: string; tool: string; input: any; outcome: string; ts: string }
  | { kind: "thought"; agent: string; text: string; ts: string }
  | { kind: "approval"; approval_id: string; tool: string; ts: string }
  | { kind: "resumed"; ts: string };

export type Notice = { id: string; kind: "done" | "failed"; title: string; body: string; session_id: string | null; agent: string | null; ts: string };
export type Message = { task?: { id: string; title: string }; auto?: boolean; fix_session?: string; id: string; role: "user" | "assistant"; text: string; ts: string; status: "done" | "working" | "waiting" | "error" | "interrupted"; activity: ActivityItem[] };
export type Session = { agent?: string | null; id: string; title: string; created: string; updated: string; status: "idle" | "running"; messages: Message[]; memory_reset?: boolean };
export type SessionSummary = { agent?: string | null; id: string; title: string; updated: string; status: string; count: number; preview: string };

export type AuditRow = { ts: string; agent: string; tool: string; input: any; outcome: string; error?: boolean; message_id?: string };

export type Email = { account?: string; id: string; from: string; to: string; subject: string; date: string; unread: boolean; snippet: string };
export type Triage = { category: "reply" | "fyi" | "ignore"; owner: string; reason: string };

export type McpServer = { name: string; state: "connected" | "error"; error: string | null; tools: number; transport: "stdio" | "http" };

export type PolicyRow = { name: string; risk: string; integration: string; description: string; decision: Decision; source: string };
export type Decision = "allow" | "ask" | "deny";
export type Settings = {
  assistant_name: string;
  company: { name: string; timezone: string; profile: string; facts: Record<string, string> };
  briefing: { time: string; days: string[]; email_to: string[] };
  policy: { defaults: Record<string, Decision>; tools: PolicyRow[]; integrations: Record<string, Decision> };
  delegates: { name: string; description: string; instructions: string; tools: string[] | null }[];
  all_tools: string[];
  provider: Record<string, any>;
  integrations: { name: string; settings: Record<string, any>; status?: EmailStatus; servers?: McpServer[] }[];
  playbooks: { name: string; text: string }[];
  usage: { date: string; input: number; output: number; cached: number; steps: number }[];
  config_path: string;
  desktop: boolean;
  claude_code: { installed: boolean; folder: string };
};

export type AgentStatus = "idle" | "working" | "waiting" | "paused";
export type AgentNow = { kind: "tool"; tool: string; input: any; outcome: string; ts: string } | { kind: "thought" | "task"; text: string; ts: string };
export type Agent = {
  id: string; name: string; log_name: string; kind: "chief" | "briefing" | "member"; role: string; instructions: string;
  tools: string[]; all_tools?: boolean; paused: boolean; held: boolean; freedom: string | null; model: string | null; model_in_use: string | null;
  rules: Record<string, Decision>; decisions: Record<string, Decision>;
  reports_to: string; status: AgentStatus; now: AgentNow | null; handed_by: string | null; session: string | null;
  waiting: string[]; asking?: boolean; never_used?: boolean; today: { done: number; problems: number; total: number };
};
export type TimelineItem = { ts: string; minute: number; tool: string; outcome: string; input: any };
export type Proposal = { role: string; name: string; description: string; instructions: string; tools: string[]; reports_to: string; freedom: string; model: string | null };
export type FreedomLevel = { key: string; label: string; explanation: string };
export type AgentsData = { day: string; today: string; agents: Agent[]; timeline: Record<string, TimelineItem[]>; request: (Job & { result?: Proposal }) | null; freedom_levels: FreedomLevel[] };

export type FaqChannel = { id: string; name: string; private: boolean; member: boolean; members: number | null };
export type FaqDoc = { name: string; entries: number; questions: number; size: number; updated: string; error: string };
export type FaqAnswer = { outcome: "answered" | "not_covered" | "not_a_question" | "no_faq"; text: string | null; used: { id: string; question: string; source: string }[] };
export type FaqQuestion = {
  id: string; ts: string; channel: string; channel_name: string; user: string; user_name: string; text: string;
  outcome: string; reply?: string; used?: string[]; error?: string; note?: string;
};
// `enabled: false` comes with no other fields.
export type Faq = {
  enabled: boolean; model: string; tone: string; documents: FaqDoc[];
  slack: {
    enabled: boolean; channels: string[]; direct_messages: boolean; answer: "everyone" | "external"; staff: string[];
    follow_ups: boolean; per_user_per_hour: number; notify_channel: string; create_task: boolean; task_owner: string;
    holding_reply: string; bot_token_set: boolean; app_token_set: boolean;
  };
  status: { state: "off" | "missing_tokens" | "connecting" | "connected" | "error"; error?: string; workspace?: string };
  reply_rule: Decision; questions: FaqQuestion[]; manifest: string; desktop: boolean;
};
