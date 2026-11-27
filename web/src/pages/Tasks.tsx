import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckSquare, Columns3, List, Pause, Play, Plus, Repeat, Search, Send, Trash2, Zap } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type AgentsData, type Schedule, type Task, type TaskStatus } from "../api";
import { Chip, Empty, Field, PageHeader, Segmented, Sheet, SkeletonList, useToast } from "../components/ui";
import { dayLabel, when } from "../lib/format";
import { STATUS } from "../lib/tools";

type Due = "all" | "overdue" | "today" | "week" | "none";
const ACTIVE: TaskStatus[] = ["open", "in_progress", "blocked"];
const BOARD: TaskStatus[] = ["open", "in_progress", "blocked", "done"];

function addDays(iso: string, n: number) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function StatusSelect({ t, onChange }: { t: Task; onChange: (s: TaskStatus) => void }) {
  return (
    <select className="status-select" data-area={STATUS[t.status]?.area} value={t.status} onChange={(e) => onChange(e.target.value as TaskStatus)} aria-label={`Status of ${t.title}`} onClick={(e) => e.stopPropagation()}>
      {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
    </select>
  );
}

function DueLabel({ t, today }: { t: Task; today: string }) {
  const late = t.due && t.due < today && ACTIVE.includes(t.status);
  const soon = t.due === today && ACTIVE.includes(t.status);
  if (!t.due) return <span className="faint">—</span>;
  return late ? <Chip hue="danger">{dayLabel(t.due, today)}</Chip> : soon ? <Chip hue="today">Today</Chip> : <span>{dayLabel(t.due, today)}</span>;
}

/** Hand a task to one of the bots: it gets the task's details and keeps it updated. */
function GiveToBot({ task, onDone }: { task: Task; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.get<AgentsData>("/api/agents") });
  const choices = (agents.data?.agents ?? []).filter((a) => a.kind !== "briefing" && a.status !== "paused");
  const [who, setWho] = useState("");
  const [note, setNote] = useState("");
  const pick = who || task.handled_by?.agent_id || "chief";
  const give = useMutation({
    mutationFn: () => api.post<{ session_id: string }>(`/api/agents/${pick}/jobs`, { text: note, task_id: task.id }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      toast(`${choices.find((a) => a.id === pick)?.name ?? "Gabe"} is on it`, "ask");
      onDone();
      nav(`/ask/${r.session_id}`);
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  return (
    <div className="give-bot" data-area="ask">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Give to a bot</strong>
        {task.handled_by && (
          <Link className="handled-by" to={`/ask/${task.handled_by.session_id}`}><Bot size={14} /> With {task.handled_by.agent}</Link>
        )}
      </div>
      <div className="form-grid">
        <Field label="Bot">
          <select className="select" value={pick} onChange={(e) => setWho(e.target.value)}>
            {choices.map((a) => <option key={a.id} value={a.id}>{a.name}{a.kind === "member" && a.role ? ` · ${a.role}` : ""}</option>)}
          </select>
        </Field>
        <Field label="Note (optional)">
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything it should know" />
        </Field>
      </div>
      <div><button type="button" className="btn btn-primary" onClick={() => give.mutate()} disabled={give.isPending || !choices.length}><Send size={15} /> Send to bot</button></div>
    </div>
  );
}

function TaskSheet({ task, owners, projects, onClose }: { task: Partial<Task> | null; owners: string[]; projects: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const isNew = !task?.id;
  const [f, setF] = useState<Partial<Task>>({ status: "open", ...task });
  const set = (k: keyof Task, v: string) => setF((x) => ({ ...x, [k]: v }));
  const save = useMutation({
    mutationFn: () => (isNew ? api.post<Task>("/api/tasks", f) : api.patch<Task>(`/api/tasks/${task!.id}`, f)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      toast(isNew ? "Task added" : "Saved", "tasks");
      onClose();
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  return (
    <Sheet title={isNew ? "New task" : "Task"} area="tasks" onClose={onClose} footer={
      <>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>{isNew ? "Add task" : "Save"}</button>
      </>
    }>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Task">
          <input className="input" value={f.title ?? ""} onChange={(e) => set("title", e.target.value)} required />
        </Field>
        <div className="form-grid">
          <Field label="Owner">
            <input className="input" list="owners" value={f.owner ?? ""} onChange={(e) => set("owner", e.target.value)} required />
          </Field>
          <Field label="Due">
            <input className="input" type="date" value={f.due ?? ""} onChange={(e) => set("due", e.target.value)} />
          </Field>
          <Field label="Status">
            <select className="select" value={f.status} onChange={(e) => set("status", e.target.value)}>
              {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </Field>
          <Field label="Project">
            <input className="input" list="projects" value={f.project ?? ""} onChange={(e) => set("project", e.target.value)} />
          </Field>
        </div>
        <Field label="Notes">
          <textarea className="textarea" value={f.notes ?? ""} onChange={(e) => set("notes", e.target.value)} />
        </Field>
        {!isNew && (
          <p className="faint">
            {task?.source === "slack" ? "From Slack" : task?.source ?? "Created by Gabe"} · {when(task?.created)}
            {task?.link && <> · <a className="link-btn" href={task.link} target="_blank" rel="noreferrer">Open in Slack</a></>}
          </p>
        )}
        {!isNew && task?.id && <GiveToBot task={task as Task} onDone={onClose} />}
        <datalist id="owners">{owners.map((o) => <option key={o} value={o} />)}</datalist>
        <datalist id="projects">{projects.map((o) => <option key={o} value={o} />)}</datalist>
        <button hidden />
      </form>
    </Sheet>
  );
}

function nextRun(ts: string) {
  const d = new Date(ts);
  const t = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return new Date().toDateString() === d.toDateString() ? t : `${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}, ${t}`;
}

function Recurring() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({ queryKey: ["schedules"], queryFn: () => api.get<Schedule[]>("/api/schedules"), refetchInterval: 15000 });
  const done = () => qc.invalidateQueries({ queryKey: ["schedules"] });
  const fail = (e: Error) => toast(e.message, "danger");
  const pause = useMutation({ mutationFn: (s: Schedule) => api.patch(`/api/schedules/${s.id}`, { paused: !s.paused }), onSuccess: done, onError: fail });
  const run = useMutation({ mutationFn: (s: Schedule) => api.post(`/api/schedules/${s.id}/run`), onSuccess: () => { done(); toast("Running", "tasks"); }, onError: fail });
  const remove = useMutation({ mutationFn: (s: Schedule) => api.del(`/api/schedules/${s.id}`), onSuccess: done, onError: fail });
  if (!data?.length) return null;
  return (
    <div className="panel table-wrap" data-area="tasks" style={{ marginBottom: 16 }}>
      <table className="table">
        <thead>
          <tr><th><Repeat size={14} /> Recurring</th><th>Who</th><th>When</th><th>Next</th><th /></tr>
        </thead>
        <tbody>
          {data.map((s) => (
            <tr key={s.id} data-done={s.paused}>
              <td>
                {s.session_id ? <Link className="link-btn" to={`/ask/${s.session_id}`}>{s.name}</Link> : s.name}
                <div className="faint">{s.instruction}</div>
              </td>
              <td>{s.member_name ?? "Gabe"}</td>
              <td>{s.when}</td>
              <td>{s.paused ? <Chip hue="neutral">Paused</Chip> : nextRun(s.next_run)}</td>
              <td>
                <div className="row" style={{ gap: 4, justifyContent: "flex-end", flexWrap: "nowrap" }}>
                  <button className="btn btn-ghost" title="Run now" aria-label={`Run ${s.name} now`} onClick={() => run.mutate(s)} disabled={run.isPending}><Zap size={15} /></button>
                  <button className="btn btn-ghost" title={s.paused ? "Resume" : "Pause"} aria-label={`${s.paused ? "Resume" : "Pause"} ${s.name}`} onClick={() => pause.mutate(s)}>{s.paused ? <Play size={15} /> : <Pause size={15} />}</button>
                  <button className="btn btn-ghost" title="Delete" aria-label={`Delete ${s.name}`} onClick={() => confirm(`Delete “${s.name}”?`) && remove.mutate(s)}><Trash2 size={15} /></button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Tasks() {
  const qc = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<"list" | "board">(() => (localStorage.getItem("gabe-task-view") as "board") || "list");
  const [editing, setEditing] = useState<Partial<Task> | null>(null);
  const [q, setQ] = useState("");
  const due = (params.get("due") as Due) || "all";
  const owner = params.get("owner") || "";
  const statusFilter = params.get("status") || "active";
  const setParam = (k: string, v: string, empty: string) => {
    const p = new URLSearchParams(params);
    if (v === empty) p.delete(k); else p.set(k, v);
    setParams(p, { replace: true });
  };

  const { data, isLoading } = useQuery({ queryKey: ["tasks"], queryFn: () => api.get<{ today: string; tasks: Task[] }>("/api/tasks"), refetchInterval: 10000 });
  const today = data?.today ?? new Date().toISOString().slice(0, 10);
  const all = data?.tasks ?? [];
  const owners = useMemo(() => [...new Set(all.map((t) => t.owner).filter(Boolean))].sort(), [all]);
  const projects = useMemo(() => [...new Set(all.map((t) => t.project).filter(Boolean))].sort(), [all]);

  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: TaskStatus }) => api.patch<Task>(`/api/tasks/${id}`, { status }),
    onMutate: ({ id, status }) => {
      qc.setQueryData<{ today: string; tasks: Task[] }>(["tasks"], (d) => d && { ...d, tasks: d.tasks.map((t) => (t.id === id ? { ...t, status } : t)) });
    },
    onSettled: () => { qc.invalidateQueries({ queryKey: ["tasks"] }); qc.invalidateQueries({ queryKey: ["overdue"] }); qc.invalidateQueries({ queryKey: ["overview"] }); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const setStatus = useCallback((id: string, status: TaskStatus) => update.mutate({ id, status }), [update]);

  const filtered = all.filter((t) => {
    if (view === "list") {
      if (statusFilter === "active" && !ACTIVE.includes(t.status)) return false;
      if (statusFilter !== "active" && statusFilter !== "all" && t.status !== statusFilter) return false;
    }
    if (owner && t.owner !== owner) return false;
    if (due === "overdue" && !(t.due && t.due < today)) return false;
    if (due === "today" && t.due !== today) return false;
    if (due === "week" && !(t.due && t.due >= today && t.due <= addDays(today, 7))) return false;
    if (due === "none" && t.due) return false;
    if (q && !`${t.title} ${t.owner} ${t.project} ${t.notes}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  }).sort((a, b) => (a.due ?? "9999").localeCompare(b.due ?? "9999"));

  const chooseView = (v: "list" | "board") => { setView(v); try { localStorage.setItem("gabe-task-view", v); } catch { /* ignore */ } };

  return (
    <>
      <PageHeader area="tasks" actions={
        <button className="btn btn-primary" data-area="tasks" onClick={() => setEditing({ due: null })}><Plus size={17} /> Add task</button>
      } />
      <div className="toolbar">
        <Segmented label="View" value={view} onChange={chooseView} options={[
          { value: "list", label: <><List size={15} /> List</> },
          { value: "board", label: <><Columns3 size={15} /> Board</> },
        ]} />
        <div className="search grow" style={{ minWidth: 180, maxWidth: 320 }}>
          <Search size={16} />
          <input className="input" placeholder="Search tasks" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search tasks" />
        </div>
        <select className="select toolbar-select" value={owner} onChange={(e) => setParam("owner", e.target.value, "")} aria-label="Owner">
          <option value="">Everyone</option>
          {owners.map((o) => <option key={o}>{o}</option>)}
        </select>
        <select className="select toolbar-select" value={due} onChange={(e) => setParam("due", e.target.value, "all")} aria-label="Due">
          <option value="all">Any due date</option>
          <option value="overdue">Overdue</option>
          <option value="today">Due today</option>
          <option value="week">Next 7 days</option>
          <option value="none">No due date</option>
        </select>
        {view === "list" && (
          <select className="select toolbar-select" value={statusFilter} onChange={(e) => setParam("status", e.target.value, "active")} aria-label="Status">
            <option value="active">Not finished</option>
            <option value="all">All</option>
            {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        )}
      </div>

      <Recurring />
      {isLoading ? (
        <div className="panel"><SkeletonList rows={5} /></div>
      ) : all.length === 0 ? (
        <div className="panel">
          <Empty hue="tasks" icon={CheckSquare} title="No tasks yet" action={<button className="btn btn-primary" data-area="tasks" onClick={() => setEditing({})}><Plus size={16} /> Add task</button>} />
        </div>
      ) : view === "list" ? (
        <div className="panel table-wrap" data-area="tasks">
          {filtered.length === 0 ? (
            <Empty hue="tasks" icon={Search} title="No matching tasks" />
          ) : (
            <table className="table">
              <thead>
                <tr><th>Task</th><th>Owner</th><th>Due</th><th>Status</th></tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr key={t.id} onClick={() => setEditing(t)} data-done={t.status === "done" || t.status === "cancelled"}>
                    <td>
                      <button className="link-btn" onClick={(e) => { e.stopPropagation(); setEditing(t); }}>{t.title}</button>
                      {t.project && <div className="faint">{t.project}</div>}
                    </td>
                    <td>
                      {t.owner}
                      {t.handled_by && ACTIVE.includes(t.status) && (
                        <div><Link className="handled-by" to={`/ask/${t.handled_by.session_id}`} onClick={(e) => e.stopPropagation()}><Bot size={13} /> {t.handled_by.agent}</Link></div>
                      )}
                    </td>
                    <td><DueLabel t={t} today={today} /></td>
                    <td><StatusSelect t={t} onChange={(s) => setStatus(t.id, s)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="board">
          {BOARD.map((col) => {
            const items = filtered.filter((t) => t.status === col);
            return (
              <section key={col} className="board-col" data-area={STATUS[col].area}>
                <h2><span className="swatch" /> {STATUS[col].label} <span className="faint">{items.length}</span></h2>
                <div className="stack" style={{ gap: 8 }}>
                  {items.map((t) => (
                    <article key={t.id} className="board-card" onClick={() => setEditing(t)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setEditing(t)}>
                      <strong>{t.title}</strong>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="faint grow">{t.owner}</span>
                        <DueLabel t={t} today={today} />
                      </div>
                      {t.handled_by && ACTIVE.includes(t.status) && <span className="handled-by"><Bot size={13} /> With {t.handled_by.agent}</span>}
                      <StatusSelect t={t} onChange={(s) => setStatus(t.id, s)} />
                    </article>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
      {editing && <TaskSheet task={editing} owners={owners} projects={projects} onClose={() => setEditing(null)} />}
    </>
  );
}
