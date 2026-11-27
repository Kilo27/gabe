import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckSquare, ChevronDown, Info, Loader2, MessageCircle, Plus, Send, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { api, type Approval, type Message, type Session, type SessionSummary, type Task } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { AttachedTask, TaskPicker } from "../components/TaskPicker";
import { useOverview } from "../components/Shell";
import { Markdown, Notice, PageHeader, SkeletonList, useToast } from "../components/ui";
import { when } from "../lib/format";
import { agentName, outcome, tool } from "../lib/tools";
import { AskBox } from "./Today";

function Steps({ m }: { m: Message }) {
  const { data: ov } = useOverview();
  const steps = m.activity.filter((a) => a.kind === "tool");
  const resumed = m.activity.some((a) => a.kind === "resumed");
  const [open, setOpen] = useState(false);
  if (!steps.length && !resumed) return null;
  const problems = steps.filter((s) => s.kind === "tool" && s.outcome !== "ok").length;
  return (
    <div className="steps">
      <button className="steps-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="steps-dots" aria-hidden>
          {steps.slice(0, 6).map((s, i) => <span key={i} className="dot" data-area={s.kind === "tool" ? tool(s.tool).area : "neutral"} />)}
        </span>
        Gabe took {steps.length} {steps.length === 1 ? "step" : "steps"}
        {problems > 0 && <span className="steps-problem"> · {problems} didn't go through</span>}
        {resumed && <span className="steps-resumed"> · picked up after a restart</span>}
        <ChevronDown size={15} style={{ rotate: open ? "180deg" : "0deg", transition: "rotate 200ms" }} />
      </button>
      {open && (
        <ol className="steps-list">
          {m.activity.map((a, i) =>
            a.kind === "tool" ? (
              <li key={i} data-area={tool(a.tool).area}>
                <span className="dot" />
                <span>
                  {a.outcome === "ok" ? tool(a.tool).past(a.input) : <>{tool(a.tool).label} <em>({outcome(a.outcome).label.toLowerCase()})</em></>}
                  {a.agent && a.agent !== ov?.assistant && <span className="faint"> · {agentName(a.agent, ov?.assistant)}</span>}
                </span>
              </li>
            ) : a.kind === "resumed" ? (
              <li key={i} className="step-resumed" data-area="activity">
                <span className="dot" />
                <span>Picked up again after the app closed</span>
              </li>
            ) : a.kind === "thought" ? (
              <li key={i} className="step-thought" data-area="neutral">
                <span className="dot" />
                <span className="muted">{a.text}</span>
              </li>
            ) : null,
          )}
        </ol>
      )}
    </div>
  );
}

function Working({ m }: { m: Message }) {
  const last = [...m.activity].reverse().find((a) => a.kind === "tool");
  return (
    <div className="working" data-area="ask">
      <Loader2 size={17} className="spin" />
      <span>{last && last.kind === "tool" ? `${tool(last.tool).past(last.input)}…` : "Gabe is thinking…"}</span>
    </div>
  );
}

function Thread({ id }: { id: string }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [text, setText] = useState("");
  const [task, setTask] = useState<Task | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const session = useQuery({
    queryKey: ["session", id],
    queryFn: () => api.get<Session>(`/api/sessions/${id}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1000 : false),
  });
  const s = session.data;
  const running = s?.status === "running";
  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.get<Approval[]>("/api/approvals"),
    refetchInterval: running ? 2000 : false,
  });
  const byId = new Map((approvals.data ?? []).map((a) => [a.id, a]));

  const send = useMutation({
    mutationFn: (t: string) => api.post<Session>(`/api/sessions/${id}/messages`, { text: t, task_id: task?.id }),
    onSuccess: (data) => {
      setText("");
      setTask(null);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.setQueryData(["session", id], data);
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/sessions/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sessions"] }); nav("/ask"); toast("Conversation deleted", "ask"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });

  const count = s?.messages.length ?? 0;
  const lastText = s?.messages[count - 1]?.text;
  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [count, lastText, running]);
  useEffect(() => {
    if (!running) qc.invalidateQueries({ queryKey: ["sessions"] });
  }, [running, qc]);

  if (session.isLoading) return <div className="thread"><SkeletonList rows={4} /></div>;
  if (!s) return <div className="thread"><Notice hue="danger" icon={AlertTriangle} title="This conversation doesn't exist any more." action={<Link className="btn btn-sm" to="/ask">Back</Link>} /></div>;

  const submit = () => (text.trim() || task) && !running && send.mutate(text.trim());

  return (
    <div className="thread">
      <div className="thread-head">
        <Link to="/ask" className="btn btn-ghost btn-sm btn-icon thread-back" aria-label="All conversations"><ArrowLeft size={18} /></Link>
        <h2 className="grow">{s.title}</h2>
        <span className="chip" data-area="ask">{s.agent ? s.agent.replace(/_/g, " ") : "Gabe"}</span>
        <button className="btn btn-ghost btn-sm btn-danger" onClick={() => confirm("Delete this conversation? This can't be undone.") && remove.mutate()} disabled={running}>
          <Trash2 size={15} /> Delete
        </button>
      </div>
      {s.memory_reset && (
        <Notice hue="ask" icon={Info} title="Gabe restarted and has forgotten this conversation. Repeat any details it needs." />
      )}
      <div className="messages" aria-live="polite">
        {s.messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="msg msg-user" data-area="ask">
              <div className="bubble">
                {m.auto && <div style={{ marginBottom: 6, fontSize: 13, opacity: 0.8 }}>Sent automatically</div>}
                {m.task && <div style={{ marginBottom: 6 }}><span className="bubble-task"><CheckSquare size={13} /> {m.task.title}</span></div>}
                {m.text}
              </div>
              <span className="faint">{when(m.ts)}</span>
            </div>
          ) : (
            <div key={m.id} className="msg msg-gabe">
              <div className="avatar" aria-hidden>G</div>
              <div className="grow stack" style={{ gap: 10 }}>
                <Steps m={m} />
                {m.activity.filter((a) => a.kind === "approval").map((a) => {
                  const ap = a.kind === "approval" ? byId.get(a.approval_id) : undefined;
                  return ap ? <ApprovalCard key={ap.id} a={ap} compact /> : null;
                })}
                {m.status === "waiting" && (
                  <div className="working" data-area="approvals"><ShieldAlert size={17} /> Waiting for approval</div>
                )}
                {m.status === "working" && <Working m={m} />}
                {m.status === "interrupted" && (
                  <div className="working" data-area="activity"><Loader2 size={17} className="spin" /> The app closed during this. Picking it back up…</div>
                )}
                {m.text && (m.status === "error" ? (
                  <Notice hue="danger" icon={AlertTriangle}>
                    {m.text}
                    {m.fix_session && <div style={{ marginTop: 8 }}><Link to={`/ask/${m.fix_session}`}>Gabe is fixing this</Link></div>}
                  </Notice>
                ) : <Markdown text={m.text} />)}
              </div>
            </div>
          ),
        )}
        <div ref={endRef} />
      </div>
      {task && <div><AttachedTask task={task} onRemove={() => setTask(null)} /></div>}
      <form className="composer" data-area="ask" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <TaskPicker value={task} onChange={setTask} disabled={running} />
        <label htmlFor="reply" className="sr-only">Message Gabe</label>
        <textarea
          id="reply"
          className="textarea"
          rows={2}
          placeholder={running ? "Gabe is working on it…" : "Reply to Gabe…"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        <button className="btn btn-primary" disabled={(!text.trim() && !task) || running || send.isPending} aria-label="Send">
          <Send size={16} /> <span className="hide-sm">Send</span>
        </button>
      </form>
    </div>
  );
}

export default function Chat() {
  const { id } = useParams();
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api.get<SessionSummary[]>("/api/sessions"),
    refetchInterval: 6000,
  });

  return (
    <>
      <div className={id ? "hide-sm" : undefined}>
        <PageHeader area="ask" />
      </div>
      <div className={`chat${id ? " chat-open" : ""}`}>
        <aside className="panel convo-list" aria-label="Conversations" data-area="ask">
          <div className="panel-head">
            <span className="swatch" />
            <h2>Conversations</h2>
            <span className="spacer" />
            <Link to="/ask" className="btn btn-primary btn-sm"><Plus size={15} /> New</Link>
          </div>
          {sessions.isLoading ? <SkeletonList rows={3} /> : !sessions.data?.length ? (
            <p className="muted" style={{ padding: 18 }}>No conversations yet.</p>
          ) : (
            <ul>
              {sessions.data.map((s) => (
                <li key={s.id}>
                  <NavLink to={`/ask/${s.id}`}>
                    <span className="convo-title">
                      {s.status === "running" && <Loader2 size={13} className="spin" />}
                      {s.title}
                    </span>
                    <span className="faint">{when(s.updated)}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          )}
        </aside>
        {id ? (
          <Thread key={id} id={id} />
        ) : (
          <div className="thread thread-new">
            <div className="new-convo">
              <div className="empty-tile" data-area="ask" style={{ background: "var(--ask)", color: "var(--on-fill)" }}><MessageCircle size={26} /></div>
              <h2>What can Gabe do for you?</h2>
            </div>
            <AskBox autoFocus />
          </div>
        )}
      </div>
    </>
  );
}
