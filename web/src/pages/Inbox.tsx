import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Inbox as InboxIcon, Loader2, MailOpen, PenLine, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Email, type EmailStatus, type Job, type Triage } from "../api";
import { GoogleButton } from "../components/GoogleButton";
import { Chip, Empty, Field, Notice, PageHeader, Segmented, Sheet, SkeletonList, useToast } from "../components/ui";
import { senderName } from "../lib/format";

type Filter = "all" | "reply" | "fyi" | "ignore" | "unsorted";
const CAT: Record<Triage["category"], { label: string; hue: "approvals" | "inbox" | "neutral" }> = {
  reply: { label: "Needs reply", hue: "approvals" },
  fyi: { label: "FYI", hue: "inbox" },
  ignore: { label: "Ignore", hue: "neutral" },
};

type InboxData = { status: EmailStatus; messages: Email[]; triage: Record<string, Triage>; job?: Job | null };

function shortDate(d: string) {
  const t = new Date(d);
  if (isNaN(+t)) return d;
  return t.toDateString() === new Date().toDateString()
    ? t.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : t.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function Reader({ m, onClose }: { m: Email; onClose: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [hint, setHint] = useState("");
  const acct = m.account ? `?account=${encodeURIComponent(m.account)}` : "";
  const full = useQuery({ queryKey: ["mail", m.id], queryFn: () => api.get<{ body: string; to: string; cc?: string }>(`/api/inbox/${m.id}${acct}`) });
  const draft = useMutation({
    mutationFn: () => api.post<{ session_id: string }>(`/api/inbox/${m.id}/draft`, { hint, subject: m.subject, from: m.from, account: m.account }),
    onSuccess: (r) => nav(`/ask/${r.session_id}`),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const read = useMutation({
    mutationFn: () => api.post(`/api/inbox/${m.id}/read${acct}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["inbox"] }); toast("Marked as read", "inbox"); onClose(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  return (
    <Sheet title={m.subject || "(no subject)"} area="inbox" onClose={onClose} footer={
      <>
        {m.unread && <button className="btn" onClick={() => read.mutate()}><Check size={16} /> Mark read</button>}
        <button className="btn btn-primary" onClick={() => draft.mutate()} disabled={draft.isPending}>
          {draft.isPending ? <Loader2 size={16} className="spin" /> : <PenLine size={16} />} Draft a reply
        </button>
      </>
    }>
      <p><strong>{senderName(m.from)}</strong> <span className="faint">· {shortDate(m.date)}{m.account && ` · to ${m.account}`}</span></p>
      {full.isLoading ? <SkeletonList rows={3} /> : <div className="letter-body mt-12">{full.data?.body}</div>}
      <div className="mt-24">
        <Field label="What should the reply say? (optional)">
          <textarea className="textarea" rows={3} value={hint} onChange={(e) => setHint(e.target.value)} />
        </Field>
      </div>
    </Sheet>
  );
}

export default function InboxPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<Email | null>(null);
  const [box, setBox] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["inbox"], queryFn: () => api.get<InboxData>("/api/inbox"), refetchInterval: 60000 });
  const job = useQuery({
    queryKey: ["job", "triage"],
    queryFn: () => api.get<Job>("/api/jobs/triage"),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  const sorting = job.data?.status === "running";
  useEffect(() => {
    if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["inbox"] });
  }, [job.data?.status, qc]);
  const sort = useMutation({
    mutationFn: () => api.post<Job>("/api/inbox/triage", { messages: data?.messages ?? [] }),
    onSuccess: (j) => qc.setQueryData(["job", "triage"], j),
    onError: (e: Error) => toast(e.message, "danger"),
  });

  const status = data?.status;
  const gjob = useQuery({
    queryKey: ["job", "gmail"],
    queryFn: () => api.get<Job>("/api/jobs/gmail"),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const signing = gjob.data?.status === "running";
  useEffect(() => {
    if (gjob.data?.status === "done") qc.invalidateQueries({ queryKey: ["inbox"] });
    if (gjob.data?.status === "error") toast(gjob.data.error ?? "Sign-in failed", "danger");
  }, [gjob.data?.status, qc]);
  const signIn = useMutation({
    mutationFn: () => api.post<Job>("/api/settings/gmail/connect"),
    onSuccess: (j) => qc.setQueryData(["job", "gmail"], j),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const boxes = (data?.status.mailboxes ?? []).filter((m) => m.connected && m.address);
  const msgs = (data?.messages ?? []).filter((m) => !box || m.account === box);
  const tri = data?.triage ?? {};
  const count = (f: Filter) => msgs.filter((m) => (f === "unsorted" ? !tri[m.id] : tri[m.id]?.category === f)).length;
  const shown = filter === "all" ? msgs : msgs.filter((m) => (filter === "unsorted" ? !tri[m.id] : tri[m.id]?.category === filter));
  const connected = status?.state === "connected";

  return (
    <>
      <PageHeader area="inbox" actions={connected && msgs.length > 0 && (
        <button className="btn btn-primary" data-area="inbox" onClick={() => sort.mutate()} disabled={sorting || sort.isPending}>
          {sorting ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />} {sorting ? "Sorting…" : "Sort with Gabe"}
        </button>
      )}>{connected && boxes.length ? boxes.map((b) => b.address).join(" · ") : null}</PageHeader>

      {isLoading ? (
        <div className="panel"><SkeletonList rows={6} /></div>
      ) : !connected ? (
        <div className="panel">
          <Empty hue={status?.state === "error" ? "danger" : "inbox"} icon={status?.state === "error" ? AlertTriangle : InboxIcon} title={status?.state === "practice" ? "Email is in practice mode" : status?.state === "error" ? "Couldn't load email" : "Gmail isn't connected"} action={
            status?.state === "disconnected" ? (
              <GoogleButton onClick={() => signIn.mutate()} disabled={signing} label={signing ? "Finish in your browser…" : "Sign in with Google"} />
            ) : <Link to="/settings/connections" className="btn btn-primary" data-area="inbox">Email settings</Link>
          }>{status?.state === "disconnected" ? null : status?.message}</Empty>
        </div>
      ) : (
        <>
          {job.data?.status === "error" && <div className="mb-16"><Notice hue="danger" icon={AlertTriangle} title="Sorting failed">{job.data.error}</Notice></div>}
          <div className="toolbar">
            <Segmented<Filter> label="Show" value={filter} onChange={setFilter} options={[
              { value: "all", label: "All", count: msgs.length },
              { value: "reply", label: "Needs reply", count: count("reply") },
              { value: "fyi", label: "FYI", count: count("fyi") },
              { value: "ignore", label: "Ignore", count: count("ignore") },
              { value: "unsorted", label: "Not sorted", count: count("unsorted") },
            ]} />
            {boxes.length > 1 && (
              <select className="select toolbar-select" value={box} onChange={(e) => setBox(e.target.value)} aria-label="Mailbox">
                <option value="">All mailboxes</option>
                {boxes.map((b) => <option key={b.address}>{b.address}</option>)}
              </select>
            )}
          </div>
          <div className="panel" data-area="inbox">
            {shown.length === 0 ? (
              <Empty hue="inbox" icon={MailOpen} title="Nothing here" />
            ) : (
              <ul className="mail-list">
                {shown.map((m) => {
                  const t = tri[m.id];
                  return (
                    <li key={m.id} data-unread={m.unread}>
                      <button className="mail-row" onClick={() => setOpen(m)}>
                        <span className="mail-from">{senderName(m.from)}</span>
                        <span className="mail-main">
                          <span className="mail-subject">{m.subject || "(no subject)"}</span>
                          <span className="faint mail-snippet">{t?.reason || m.snippet}</span>
                        </span>
                        <span className="mail-meta">
                          {t && <Chip hue={CAT[t.category].hue} dot>{CAT[t.category].label}</Chip>}
                          {t?.owner && <span className="faint">{t.owner}</span>}
                          {boxes.length > 1 && !box && m.account && <span className="chip" data-area="inbox" title={m.account}>{m.account.split("@")[0]}</span>}
                          <span className="faint">{shortDate(m.date)}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
      {open && <Reader m={open} onClose={() => setOpen(null)} />}
    </>
  );
}
