import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowRight, CalendarClock, CheckCircle2, Clock, FileText, Loader2, Mail, OctagonAlert, RefreshCw, Send, ShieldCheck, Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type AuditRow, type Job, type Session } from "../api";
import { useOverview } from "../components/Shell";
import { Empty, Markdown, Notice, PageHeader, Skeleton, SkeletonList, useToast } from "../components/ui";
import { greeting, longDate, plural } from "../lib/format";
import { ActivityRow } from "./Activity";

const SUGGESTIONS = ["What's overdue?", "Summarise today's email", "Add a task", "What did we decide about…"];

export function AskBox({ autoFocus, compact }: { autoFocus?: boolean; compact?: boolean }) {
  const [text, setText] = useState("");
  const nav = useNavigate();
  const toast = useToast();
  const start = useMutation({
    mutationFn: (t: string) => api.post<Session>("/api/sessions", { text: t }),
    onSuccess: (s) => nav(`/ask/${s.id}`),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const submit = () => text.trim() && start.mutate(text.trim());
  return (
    <div className="ask-box" data-area="ask">
      <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label htmlFor="ask-input" className="sr-only">Ask Gabe</label>
        <textarea
          id="ask-input"
          className="textarea"
          rows={compact ? 2 : 3}
          autoFocus={autoFocus}
          placeholder="Ask Gabe…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
        />
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span />
          <button className="btn btn-primary" disabled={!text.trim() || start.isPending}>
            {start.isPending ? <Loader2 size={17} className="spin" /> : <Send size={16} />} Send
          </button>
        </div>
      </form>
      <div className="suggestions" aria-label="Ideas">
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="suggestion" onClick={() => setText(s)}>
            <Sparkles size={14} /> {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function NeedsYou() {
  const { data, isLoading } = useOverview();
  if (isLoading || !data) return <div className="panel"><SkeletonList rows={3} /></div>;
  const t = data.tasks;
  const rows: { hue: string; icon: any; text: string; to: string; cta: string }[] = [];
  if (data.approvals_pending) rows.push({ hue: "approvals", icon: ShieldCheck, text: `${plural(data.approvals_pending, "thing")} ${data.approvals_pending === 1 ? "is" : "are"} waiting for your OK`, to: "/approvals", cta: "Review" });
  if (t.overdue) rows.push({ hue: "danger", icon: AlertTriangle, text: `${plural(t.overdue, "task")} ${t.overdue === 1 ? "is" : "are"} overdue`, to: "/tasks?due=overdue", cta: "See them" });
  if (t.due_today) rows.push({ hue: "tasks", icon: Clock, text: `${plural(t.due_today, "task")} due today`, to: "/tasks?due=today", cta: "See them" });
  if (t.blocked) rows.push({ hue: "danger", icon: OctagonAlert, text: `${plural(t.blocked, "task")} ${t.blocked === 1 ? "is" : "are"} blocked`, to: "/tasks?status=blocked", cta: "Unblock" });
  if (t.due_week) rows.push({ hue: "tasks", icon: CalendarClock, text: `${plural(t.due_week, "task")} due in the next 7 days`, to: "/tasks?due=week", cta: "Plan" });
  if (data.email.state !== "connected" && data.email.state !== "practice") rows.push({ hue: "inbox", icon: Mail, text: "Gmail isn't connected", to: "/settings/email", cta: "Fix" });

  return (
    <section className="panel" aria-labelledby="needs-you">
      <div className="panel-head" data-area="approvals">
        <span className="swatch" />
        <h2 id="needs-you">Needs you</h2>
      </div>
      {rows.length === 0 ? (
        <div className="caught-up" data-area="tasks">
          <CheckCircle2 size={22} />
          <div>
            <strong>You're all caught up.</strong>
          </div>
        </div>
      ) : (
        <ul className="needs">
          {rows.map((r) => (
            <li key={r.text} data-area={r.hue}>
              <Link to={r.to}>
                <span className="needs-icon"><r.icon size={18} /></span>
                <span className="grow">{r.text}</span>
                <span className="needs-cta">{r.cta} <ArrowRight size={15} /></span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Brief() {
  const { data: ov } = useOverview();
  const qc = useQueryClient();
  const toast = useToast();
  const [date, setDate] = useState<string | null>(null);
  const briefs = useQuery({ queryKey: ["briefs"], queryFn: () => api.get<{ date: string; title: string }[]>("/api/briefs") });
  const shown = date ?? briefs.data?.[0]?.date ?? null;
  const brief = useQuery({
    queryKey: ["brief", shown],
    queryFn: () => api.get<{ date: string; text: string }>(`/api/briefs/${shown}`),
    enabled: !!shown,
  });
  const job = useQuery({
    queryKey: ["job", "brief"],
    queryFn: () => api.get<Job>("/api/jobs/brief"),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
    initialData: ov?.brief_job ?? undefined,
  });
  const running = job.data?.status === "running";
  useEffect(() => {
    if (job.data?.status === "done") {
      qc.invalidateQueries({ queryKey: ["briefs"] });
      qc.invalidateQueries({ queryKey: ["brief"] });
    }
  }, [job.data?.status, qc]);

  const write = useMutation({
    mutationFn: () => api.post<Job>("/api/briefs/run"),
    onSuccess: (j) => { qc.setQueryData(["job", "brief"], j); setDate(null); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const email = useMutation({
    mutationFn: () => api.post<{ sent_to: string[] }>(`/api/briefs/${shown}/email`),
    onSuccess: (r) => toast(`Brief emailed to ${r.sent_to.join(", ")}`, "today"),
    onError: (e: Error) => toast(e.message, "danger"),
  });

  return (
    <section className="panel brief" data-area="today" aria-labelledby="brief-title">
      <div className="panel-head">
        <span className="swatch" />
        <h2 id="brief-title">Daily brief</h2>
        <span className="spacer" />
        {briefs.data && briefs.data.length > 1 && (
          <select className="select brief-pick" aria-label="Show the brief from another day" value={shown ?? ""} onChange={(e) => setDate(e.target.value)}>
            {briefs.data.map((b) => (
              <option key={b.date} value={b.date}>{longDate(b.date)}</option>
            ))}
          </select>
        )}
      </div>
      <div className="panel-pad">
        {running && (
          <div style={{ marginBottom: 16 }}>
            <Notice hue="today" icon={Loader2} title="Writing a fresh brief…" />
          </div>
        )}
        {job.data?.status === "error" && (
          <div style={{ marginBottom: 16 }}>
            <Notice hue="danger" icon={AlertTriangle} title="The brief couldn't be written">{job.data.error}</Notice>
          </div>
        )}
        {briefs.isLoading || (shown && brief.isLoading) ? (
          <div className="stack"><Skeleton h={24} w="55%" /><Skeleton h={14} /><Skeleton h={14} w="90%" /><Skeleton h={14} w="70%" /></div>
        ) : !shown ? (
          <Empty hue="today" icon={FileText} title="No brief yet" action={
            <button className="btn btn-primary" onClick={() => write.mutate()} disabled={running}>
              <RefreshCw size={16} /> Write today's brief
            </button>
          } />
        ) : (
          <>
            {shown !== ov?.today && (
              <p className="faint" style={{ marginBottom: 12 }}>This brief is from {longDate(shown)}.</p>
            )}
            {brief.data && <Markdown text={brief.data.text} />}
          </>
        )}
      </div>
      {shown && (
        <div className="panel-foot">
          <button className="btn btn-primary btn-sm" onClick={() => write.mutate()} disabled={running || write.isPending}>
            {running ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} {running ? "Writing…" : "Write a fresh brief"}
          </button>
          <button className="btn btn-sm" onClick={() => email.mutate()} disabled={email.isPending}>
            <Mail size={15} /> Email this brief
          </button>
          {ov?.next_brief && (
            <span className="faint">Next: {new Date(ov.next_brief).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}</span>
          )}
        </div>
      )}
    </section>
  );
}

function Recent() {
  const { data, isLoading } = useQuery({
    queryKey: ["activity", "recent"],
    queryFn: () => api.get<{ rows: AuditRow[] }>("/api/activity?limit=6"),
    refetchInterval: 8000,
  });
  return (
    <section className="panel" data-area="activity" aria-labelledby="recent-title">
      <div className="panel-head">
        <span className="swatch" />
        <h2 id="recent-title">Recent activity</h2>
        <span className="spacer" />
        <Link to="/activity" className="btn btn-ghost btn-sm">See all <ArrowRight size={15} /></Link>
      </div>
      {isLoading ? <SkeletonList rows={3} /> : !data?.rows.length ? (
        <p className="muted" style={{ padding: 18 }}>Nothing yet.</p>
      ) : (
        <ul className="activity-list">
          {data.rows.map((r, i) => <ActivityRow key={i} row={r} compact />)}
        </ul>
      )}
    </section>
  );
}

export default function Today() {
  const { data } = useOverview();
  return (
    <>
      <PageHeader area="today" title={greeting()}>
        {data ? longDate(data.today) : null}
      </PageHeader>
      <div className="today-grid">
        <div className="stack">
          <NeedsYou />
          <Brief />
        </div>
        <div className="stack">
          <section className="panel" aria-labelledby="ask-title" data-area="ask">
            <div className="panel-head">
              <span className="swatch" />
              <h2 id="ask-title">Ask Gabe</h2>
            </div>
            <div className="panel-pad"><AskBox compact /></div>
          </section>
          <Recent />
        </div>
      </div>
    </>
  );
}
