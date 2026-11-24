import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, FileText, Loader2, MessageCircle, Pause, Play, Plus, Send, ShieldAlert, SlidersHorizontal, Sparkles, Trash2, User } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Agent, type AgentsData, type Approval, type Decision, type Job, type Proposal, type Task, type SessionSummary, type TimelineItem } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { AttachedTask, TaskPicker } from "../components/TaskPicker";
import { Field, Notice, PageHeader, Segmented, Sheet, SkeletonList, useToast } from "../components/ui";
import { MODELS, agentHue, agentLabel, modelLabel, nowText } from "../lib/agents";
import type { Area } from "../lib/areas";
import { when } from "../lib/format";
import { DECISION_WORDS, outcome, tool } from "../lib/tools";

type Hued = Agent & { hue: Area };

function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: () => api.get<AgentsData>("/api/agents"),
    refetchInterval: (q) => (q.state.data?.agents.some((a) => a.status === "working") || q.state.data?.request?.status === "running" ? 1000 : 4000),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["agents"] });
    qc.invalidateQueries({ queryKey: ["overview"] });
  };
}

/* ---- Map --------------------------------------------------------------------- */

type Pt = { x: number; y: number };

/** Org chart, left to right: You → Gabe → Gabe's reports → their reports … */
function AgentMap({ agents, onOpen }: { agents: Hued[]; onOpen: (a: Hued) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const byId = new Map(agents.map((a) => [a.id, a]));
  const kids = (id: string) => agents.filter((a) => a.reports_to === id);
  const chief = agents.find((a) => a.kind === "chief")!;

  // Rows: one per leaf, in tree order; a manager sits level with the middle of its reports.
  const ROW = 78;
  const depth = new Map<string, number>();
  const row = new Map<string, number>();
  let leaves = 0;
  const place = (a: Hued, d: number): number => {
    depth.set(a.id, d);
    const ks = kids(a.id);
    const r = ks.length ? ks.map((k) => place(k, d + 1)).reduce((x, y) => x + y, 0) / ks.length : leaves++;
    row.set(a.id, r);
    return r;
  };
  place(chief, 1);
  const maxDepth = Math.max(...depth.values());
  const H = Math.max(200, leaves * ROW + 40);
  const youW = 64;
  const colW = (w - youW - 24) / maxDepth;
  const nodeW = Math.max(132, Math.min(236, colW - 28));
  const pos = (id: string): Pt => ({ x: youW + 12 + (depth.get(id)! - 1) * colW + nodeW / 2, y: 20 + row.get(id)! * ROW + ROW / 2 });
  const you: Pt = { x: 30, y: pos(chief.id).y };

  const curve = (a: Pt, b: Pt, fromW: number, toW: number) => {
    const x1 = a.x + fromW / 2, x2 = b.x - toW / 2, mx = (x1 + x2) / 2;
    return `M${x1},${a.y} C${mx},${a.y} ${mx},${b.y} ${x2},${b.y}`;
  };

  const edges: { d: string; hue: Area; active: boolean; dashed?: boolean; key: string }[] = [
    { key: "you-chief", d: curve(you, pos(chief.id), 40, nodeW), hue: chief.hue, active: chief.status === "working" && chief.handed_by === "you" },
  ];
  for (const a of agents) {
    if (a === chief) continue;
    const boss = byId.get(a.reports_to);
    if (!boss) continue;
    const direct = a.handed_by === "you" && a.status === "working";
    edges.push({ key: `${boss.id}-${a.id}`, d: curve(pos(boss.id), pos(a.id), nodeW, nodeW), hue: a.hue,
      active: a.status === "working" && a.handed_by === boss.id });
    if (direct) edges.push({ key: `you-${a.id}`, d: curve(you, pos(a.id), 40, nodeW), hue: a.hue, active: true, dashed: true });
  }

  return (
    <div className="map" ref={ref} style={{ height: H }}>
      <svg width={w} height={H} aria-hidden>
        {edges.map((e) => (
          <path key={e.key} d={e.d} data-area={e.hue} className={`edge${e.active ? " edge-on" : ""}${e.dashed ? " edge-direct" : ""}`} />
        ))}
      </svg>
      <div className="map-you" style={{ left: you.x, top: you.y }}>
        <span className="map-you-tile"><User size={18} /></span>
        <span>You</span>
      </div>
      {agents.map((a) => {
        if (!depth.has(a.id)) return null;
        const p = pos(a.id);
        return (
          <button key={a.id} className="node" data-area={a.hue} data-status={a.status} data-never={a.never_used || undefined} style={{ left: p.x, top: p.y, width: nodeW }} onClick={() => onOpen(a)} aria-label={`${agentLabel(a)}: ${nowText(a)}`}>
            <span className="node-tile">{a.kind === "briefing" ? <FileText size={17} /> : a.kind === "chief" ? <Sparkles size={17} /> : <Bot size={17} />}</span>
            <span className="node-text">
              <strong>{agentLabel(a)}</strong>
              <span className="node-now">
                {a.status === "working" && <Loader2 size={12} className="spin" />}
                {nowText(a)}
              </span>
            </span>
            {a.waiting.length > 0 && <span className="node-badge" aria-hidden>{a.waiting.length}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* ---- Timelines --------------------------------------------------------------- */

function hhmm(m: number) {
  const d = new Date();
  d.setHours(Math.floor(m / 60), m % 60, 0, 0);
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: m % 60 ? "2-digit" : undefined });
}

function Timelines({ agents, timeline, isToday }: { agents: Hued[]; timeline: Record<string, TimelineItem[]>; isToday: boolean }) {
  const all = Object.values(timeline).flat();
  const nowMin = new Date().getHours() * 60 + new Date().getMinutes();
  const lo = Math.min(7 * 60, ...all.map((i) => i.minute - 30));
  const hi = Math.max(19 * 60, ...all.map((i) => i.minute + 30), isToday ? nowMin + 30 : 0);
  const start = Math.floor(lo / 60) * 60;
  const end = Math.ceil(hi / 60) * 60;
  const pct = (m: number) => ((m - start) / (end - start)) * 100;
  const hours: number[] = [];
  for (let m = start; m <= end; m += 120) hours.push(m);
  const lanes: { id: string; label: string; hue: Area | "neutral"; items: TimelineItem[] }[] = [
    ...agents.map((a) => ({ id: a.id, label: agentLabel(a), hue: a.hue, items: timeline[a.id] ?? [] })),
    { id: "you", label: "People", hue: "neutral" as const, items: timeline.you ?? [] },
  ];

  return (
    <div className="lanes" role="table" aria-label="What each agent did today">
      <div className="lane lane-axis" role="row" aria-hidden>
        <span className="lane-label" />
        <div className="lane-track">
          {hours.map((m) => <span key={m} className="tick" style={{ left: `${pct(m)}%` }}>{hhmm(m)}</span>)}
        </div>
        <span className="lane-count" />
      </div>
      {lanes.map((l) => (
        <div className="lane" role="row" key={l.id} data-area={l.hue}>
          <span className="lane-label" role="rowheader"><span className="dot" /> {l.label}</span>
          <div className="lane-track" role="cell">
            {hours.map((m) => <span key={m} className="grid" style={{ left: `${pct(m)}%` }} />)}
            {isToday && <span className="now-line" style={{ left: `${pct(nowMin)}%` }} />}
            {l.items.map((it, i) => {
              const t = tool(it.tool);
              const label = `${hhmm(it.minute)} · ${it.outcome === "ok" ? t.past(it.input) : `${t.label} (${outcome(it.outcome).label.toLowerCase()})`}`;
              return (
                <span key={i} className={`blip${it.outcome !== "ok" ? " blip-bad" : ""}`} data-area={t.area} style={{ left: `${pct(it.minute)}%` }} tabIndex={0} aria-label={label}>
                  <span className="blip-tip">{label}</span>
                </span>
              );
            })}
          </div>
          <span className="lane-count">{l.items.length || "–"}</span>
        </div>
      ))}
    </div>
  );
}

/* ---- Agent card --------------------------------------------------------------- */

function Card({ a, boss, onOpen }: { a: Hued; boss?: Hued; onOpen: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const inval = useInvalidate();
  const [job, setJob] = useState("");
  const [task, setTask] = useState<Task | null>(null);
  const pause = useMutation({
    mutationFn: () => api.post(`/api/agents/${a.id}/pause`, { paused: !a.paused }),
    onSuccess: () => { inval(); toast(a.paused ? `${agentLabel(a)} resumed` : `${agentLabel(a)} paused`, a.hue); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const give = useMutation({
    mutationFn: () => api.post<{ session_id?: string; job?: Job }>(`/api/agents/${a.id}/jobs`, { text: job, task_id: task?.id }),
    onSuccess: (r) => {
      setJob("");
      setTask(null);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      inval();
      toast(r.session_id ? `${agentLabel(a)} is on it` : "Writing a fresh brief", a.hue);
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });

  return (
    <article className="agent" data-area={a.hue} data-status={a.status}>
      <header className="agent-head">
        <span className="node-tile">{a.kind === "briefing" ? <FileText size={18} /> : a.kind === "chief" ? <Sparkles size={18} /> : <Bot size={18} />}</span>
        <div className="grow">
          <h3>{agentLabel(a)}</h3>
          <p className="faint">{a.role}</p>
          {a.kind === "member" && boss && <p className="agent-boss"><span className="dot" data-area={boss.hue} /> Reports to {agentLabel(boss)}</p>}
        </div>
        <span className="agent-state" data-never={a.never_used || undefined}>{a.status === "working" ? "Working" : a.status === "waiting" ? "Waiting" : a.status === "paused" ? "Paused" : a.never_used ? "Never used" : "Idle"}</span>
      </header>

      {a.status === "working" && (
        <div className="agent-now">
          <Loader2 size={14} className="spin" />
          <span>{nowText(a)}</span>
        </div>
      )}

      <div className="agent-stats">
        <span><strong>{a.today.done}</strong> done today</span>
        {a.today.problems > 0 && <span className="agent-bad"><strong>{a.today.problems}</strong> stopped</span>}
      </div>

      {a.waiting.length > 0 && (
        <button className="btn btn-sm agent-review" onClick={onOpen}>
          <ShieldAlert size={15} /> {a.asking ? "Answer" : "Review"} {a.waiting.length === 1 ? (a.asking ? "question" : "request") : `${a.waiting.length} items`}
        </button>
      )}

      {a.kind === "briefing" ? (
        <div className="row">
          <button className="btn btn-primary btn-sm" onClick={() => give.mutate()} disabled={a.paused || a.status === "working"}>
            {a.status === "working" ? <Loader2 size={15} className="spin" /> : <FileText size={15} />} Write brief now
          </button>
        </div>
      ) : (
        <div className="agent-job-wrap">
          {task && <div><AttachedTask task={task} onRemove={() => setTask(null)} /></div>}
          <form className="agent-job" onSubmit={(e) => { e.preventDefault(); if (job.trim() || task) give.mutate(); }}>
            <TaskPicker value={task} onChange={setTask} disabled={a.paused} />
            <label className="sr-only" htmlFor={`job-${a.id}`}>Give {agentLabel(a)} a job</label>
            <input id={`job-${a.id}`} className="input" placeholder={a.paused ? "Paused" : task ? "Add a note (optional)" : "Give it a job…"} value={job} onChange={(e) => setJob(e.target.value)} disabled={a.paused} />
            <button className="btn btn-primary btn-icon" aria-label="Send job" disabled={(!job.trim() && !task) || give.isPending || a.paused}>
              {give.isPending ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
            </button>
          </form>
        </div>
      )}

      <footer className="agent-foot">
        <button className="btn btn-ghost btn-sm" onClick={() => pause.mutate()} disabled={pause.isPending}>
          {a.paused ? <><Play size={15} /> Resume</> : <><Pause size={15} /> Pause</>}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onOpen}><SlidersHorizontal size={15} /> Manage</button>
        {a.session && a.kind !== "briefing" && (
          <Link className="btn btn-ghost btn-sm" to={`/ask/${a.session}`}><MessageCircle size={15} /> Last job</Link>
        )}
      </footer>
    </article>
  );
}

/* ---- Agent sheet: approvals, manager, freedom, actions, jobs ------------------- */

function FreedomPick({ value, onChange, levels }: { value: string; onChange: (v: string) => void; levels: AgentsData["freedom_levels"] }) {
  return (
    <div className="freedom" role="radiogroup" aria-label="Freedom">
      {levels.map((f) => (
        <label key={f.key} className="freedom-opt" data-on={value === f.key}>
          <input type="radio" name="freedom" value={f.key} checked={value === f.key} onChange={() => onChange(f.key)} />
          <strong>{f.label}</strong>
          <span className="faint">{f.explanation}</span>
        </label>
      ))}
    </div>
  );
}

function ModelPick({ value, onChange, lastUsed }: { value: string | null | undefined; onChange: (v: string | null) => void; lastUsed?: string | null }) {
  return (
    <select className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{lastUsed ? `Default (last job: ${modelLabel(lastUsed)})` : "Default"}</option>
      {Object.entries(MODELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select>
  );
}

function ManagerPick({ value, onChange, agents, self, auto }: { value: string; onChange: (v: string) => void; agents: Hued[]; self?: string; auto?: boolean }) {
  // Can't report to yourself or to anyone under you.
  const under = new Set<string>();
  if (self) {
    const walk = (id: string) => agents.filter((x) => x.reports_to === id).forEach((x) => { under.add(x.id); walk(x.id); });
    walk(self);
  }
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {auto && <option value="">Default</option>}
      {agents.filter((x) => x.kind !== "briefing" && x.id !== self && !under.has(x.id)).map((x) => (
        <option key={x.id} value={x.id}>{agentLabel(x)}</option>
      ))}
    </select>
  );
}

function AgentSheet({ a, agents, levels, onClose }: { a: Hued; agents: Hued[]; levels: AgentsData["freedom_levels"]; onClose: () => void }) {
  const toast = useToast();
  const inval = useInvalidate();
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api.get<Approval[]>("/api/approvals"), refetchInterval: 3000 });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api.get<SessionSummary[]>("/api/sessions") });
  const mine = (approvals.data ?? []).filter((x) => x.status === "pending" && x.agent === a.log_name);
  const convos = (sessions.data ?? []).filter((s) => (a.kind === "chief" ? !s.agent : s.agent === a.id)).slice(0, 5);
  const member = a.kind === "member";
  const [rules, setRules] = useState<Record<string, Decision>>(a.rules);
  const [name, setName] = useState(a.name);
  const [role, setRole] = useState(a.role);
  const [instructions, setInstructions] = useState(a.instructions);
  const [tools, setTools] = useState<string[]>(a.tools.filter((t) => t !== "delegate"));
  const [manager, setManager] = useState(a.reports_to);
  const [freedom, setFreedom] = useState(a.freedom ?? "standard");
  const [model, setModel] = useState<string | null>(a.model);
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<{ all_tools: string[] }>("/api/settings"), enabled: member });

  const save = useMutation({
    mutationFn: async () => {
      if (a.kind === "chief") await api.put("/api/agents/chief/rules", { rules });
      if (member) await api.put(`/api/agents/${a.id}`, { name, description: role, instructions, tools, reports_to: manager, freedom, model });
    },
    onSuccess: () => { inval(); toast("Saved", a.hue); onClose(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/agents/${a.id}`),
    onSuccess: () => { inval(); toast(`${agentLabel(a)} removed`, a.hue); onClose(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });

  const decision = (t: string): Decision => rules[t] ?? a.decisions[t] ?? "allow";

  return (
    <Sheet title={agentLabel(a)} area={a.hue} onClose={onClose} footer={
      a.kind === "briefing" ? undefined : (
        <>
          {member && <button className="btn btn-ghost btn-danger" onClick={() => confirm(`Remove ${agentLabel(a)}? Anyone reporting to it moves up.`) && remove.mutate()}><Trash2 size={15} /> Remove</button>}
          <span className="grow" />
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
        </>
      )
    }>
      <div className="stack" style={{ gap: 22 }}>
        {mine.length > 0 && (
          <div className="stack" style={{ gap: 12 }}>
            {mine.map((x) => <ApprovalCard key={x.id} a={x} compact />)}
          </div>
        )}

        {member ? (
          <>
            <div className="form-grid">
              <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <Field label="Reports to"><ManagerPick value={manager} onChange={setManager} agents={agents} self={a.id} /></Field>
            </div>
            <div className="form-grid">
              <Field label="Job"><input className="input" value={role} onChange={(e) => setRole(e.target.value)} /></Field>
              <Field label="Model"><ModelPick value={model} onChange={setModel} lastUsed={a.model ? null : a.model_in_use} /></Field>
            </div>
            <Field label="Instructions"><textarea className="textarea" rows={4} value={instructions} onChange={(e) => setInstructions(e.target.value)} /></Field>
            <div className="field"><span>Freedom</span><FreedomPick value={freedom} onChange={setFreedom} levels={levels} /></div>
            <div className="field">
              <span>What it can do</span>
              <div className="day-picks">
                {(settings.data?.all_tools ?? tools).map((t) => {
                  const on = tools.includes(t);
                  return <button key={t} type="button" aria-pressed={on} onClick={() => setTools(on ? tools.filter((x) => x !== t) : [...tools, t])}>{tool(t).label}</button>;
                })}
              </div>
            </div>
          </>
        ) : (
          <>
            <p className="muted">{a.role}</p>
            <div className="field"><span>Model</span><strong>{modelLabel(a.model_in_use)}</strong></div>
            <div className="field">
              <span>{a.kind === "chief" ? "What it does itself" : "What it may do"}</span>
              <div className="perm-list">
                {a.tools.map((t) => (
                  <div className="perm" key={t}>
                    <span className="grow">{tool(t).label}</span>
                    {a.kind === "briefing" ? <span className="faint">Read only</span> : (
                      <Segmented<Decision> label={tool(t).label} value={decision(t)} onChange={(v) => setRules({ ...rules, [t]: v })} options={(["allow", "ask", "deny"] as Decision[]).map((d) => ({
                        value: d, label: <><span className="dot" data-area={DECISION_WORDS[d].area} /> {DECISION_WORDS[d].label}</>,
                      }))} />
                    )}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {a.kind !== "briefing" && (
          <div className="field">
            <span>Recent jobs</span>
            {convos.length === 0 ? <p className="faint">None yet.</p> : (
              <ul className="job-list">
                {convos.map((s) => (
                  <li key={s.id}><Link to={`/ask/${s.id}`}><MessageCircle size={14} /> <span className="grow">{s.title}</span> <span className="faint">{when(s.updated)}</span></Link></li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Sheet>
  );
}

/* ---- Request a new agent ------------------------------------------------------ */

function RequestSheet({ request, agents, levels, onClose }: { request: AgentsData["request"]; agents: Hued[]; levels: AgentsData["freedom_levels"]; onClose: () => void }) {
  const toast = useToast();
  const inval = useInvalidate();
  const [role, setRole] = useState("");
  const [manager, setManager] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [freedom, setFreedom] = useState("standard");
  const [draft, setDraft] = useState<Proposal | null>(null);
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<{ all_tools: string[] }>("/api/settings") });
  const running = request?.status === "running";
  useEffect(() => {
    if (request?.status === "done" && request.result && !draft) setDraft(request.result);
  }, [request, draft]);

  const ask = useMutation({
    mutationFn: () => api.post("/api/agents/request", { role, reports_to: manager || null, model, freedom }),
    onSuccess: () => { setDraft(null); inval(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const add = useMutation({
    mutationFn: () => api.post("/api/agents", draft),
    onSuccess: async () => { await api.post("/api/agents/request/dismiss"); inval(); toast(`${draft!.name} added`, "tasks"); onClose(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const discard = async () => { await api.post("/api/agents/request/dismiss"); setDraft(null); inval(); };

  return (
    <Sheet title="Request an agent" area="ask" onClose={onClose} footer={draft ? (
      <>
        <button className="btn btn-ghost" onClick={discard}>Discard</button>
        <button className="btn btn-primary" onClick={() => add.mutate()} disabled={add.isPending}>Add agent</button>
      </>
    ) : undefined}>
      {!draft ? (
        <form className="stack" onSubmit={(e) => { e.preventDefault(); if (role.trim()) ask.mutate(); }}>
          <Field label="Role">
            <textarea className="textarea" rows={3} value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Chases unpaid invoices" disabled={running} />
          </Field>
          <div className="form-grid">
            <Field label="Reports to"><ManagerPick value={manager} onChange={setManager} agents={agents} auto /></Field>
            <Field label="Model"><ModelPick value={model} onChange={setModel} /></Field>
          </div>
          <div className="field"><span>Freedom</span><FreedomPick value={freedom} onChange={setFreedom} levels={levels.filter((f) => f.key !== "trusted")} /></div>
          {request?.status === "error" && <Notice hue="danger" icon={AlertTriangle}>{request.error}</Notice>}
          <div>
            <button className="btn btn-primary" disabled={!role.trim() || running || ask.isPending}>
              {running ? <><Loader2 size={16} className="spin" /> Gabe is designing it…</> : <><Sparkles size={16} /> Ask Gabe</>}
            </button>
          </div>
        </form>
      ) : (
        <div className="stack">
          <div className="form-grid">
            <Field label="Name"><input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
            <Field label="Reports to"><ManagerPick value={draft.reports_to} onChange={(v) => setDraft({ ...draft, reports_to: v })} agents={agents} /></Field>
          </div>
          <div className="form-grid">
            <Field label="Job"><input className="input" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
            <Field label="Model"><ModelPick value={draft.model} onChange={(v) => setDraft({ ...draft, model: v })} /></Field>
          </div>
          <Field label="Instructions"><textarea className="textarea" rows={5} value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} /></Field>
          <div className="field"><span>Freedom</span><FreedomPick value={draft.freedom} onChange={(v) => setDraft({ ...draft, freedom: v })} levels={levels} /></div>
          <div className="field">
            <span>What it can do</span>
            <div className="day-picks">
              {(settings.data?.all_tools ?? draft.tools).map((t) => {
                const on = draft.tools.includes(t);
                return <button key={t} type="button" aria-pressed={on} onClick={() => setDraft({ ...draft, tools: on ? draft.tools.filter((x) => x !== t) : [...draft.tools, t] })}>{tool(t).label}</button>;
              })}
            </div>
          </div>
        </div>
      )}
    </Sheet>
  );
}

/* ---- Page --------------------------------------------------------------------- */

export default function Agents() {
  const { data, isLoading } = useAgents();
  const [openId, setOpenId] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const agents: Hued[] = useMemo(() => (data?.agents ?? []).map((a, _, all) => ({ ...a, hue: agentHue(a, all) })), [data]);
  const open = agents.find((a) => a.id === openId);
  const working = agents.filter((a) => a.status === "working").length;
  const waiting = agents.reduce((n, a) => n + a.waiting.length, 0);

  return (
    <>
      <PageHeader area="ask" title="Agents" actions={
        <button className="btn btn-primary" onClick={() => setRequesting(true)}>
          {data?.request?.status === "running" ? <Loader2 size={16} className="spin" /> : <Plus size={17} />} Request an agent
        </button>
      }>
        {data ? `${agents.length} agents · ${working} working${waiting ? ` · ${waiting} waiting for you` : ""}` : null}
      </PageHeader>

      {isLoading || !data ? (
        <div className="panel"><SkeletonList rows={5} /></div>
      ) : (
        <div className="stack" style={{ gap: 20 }}>
          <section className="panel map-panel" aria-label="Who works for whom">
            <AgentMap agents={agents} onOpen={(a) => setOpenId(a.id)} />
          </section>

          <div className="agent-grid">
            {agents.map((a) => <Card key={a.id} a={a} boss={agents.find((b) => b.id === a.reports_to)} onOpen={() => setOpenId(a.id)} />)}
          </div>

          <section className="panel" aria-labelledby="today-lanes">
            <div className="panel-head" data-area="activity">
              <span className="swatch" />
              <h2 id="today-lanes">Today</h2>
              <span className="spacer" />
              <Link to="/activity" className="btn btn-ghost btn-sm">Full log</Link>
            </div>
            <Timelines agents={agents} timeline={data.timeline} isToday={data.day === data.today} />
          </section>
        </div>
      )}

      {open && <AgentSheet key={open.id} a={open} agents={agents} levels={data?.freedom_levels ?? []} onClose={() => setOpenId(null)} />}
      {requesting && <RequestSheet request={data?.request ?? null} agents={agents} levels={data?.freedom_levels ?? []} onClose={() => setRequesting(false)} />}
    </>
  );
}
