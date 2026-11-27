import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Copy, ExternalLink, FileText, Hash, Loader2, Lock, MessageCircleQuestion, Send, Trash2, Upload } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { api, native, type Faq, type FaqAnswer, type FaqChannel, type FaqDoc, type FaqQuestion } from "../api";
import { Chip, Field, Notice, Segmented, SkeletonList, useToast } from "../components/ui";
import { plural, when } from "../lib/format";
import { DECISION_WORDS } from "../lib/tools";

const ACCEPT = ".txt,.md,.markdown,.csv,.tsv,.json,.docx";
const NEW_APP_URL = "https://api.slack.com/apps?new_app=1";
const OUTCOMES: Record<string, { label: string; hue: "tasks" | "approvals" | "danger" | "neutral" }> = {
  answered: { label: "Answered", hue: "tasks" },
  unanswered: { label: "No answer", hue: "approvals" },
  declined: { label: "Not approved", hue: "neutral" },
  blocked: { label: "Blocked by rules", hue: "danger" },
  rate_limited: { label: "Too many", hue: "neutral" },
  error: { label: "Failed", hue: "danger" },
};
const STATUS: Record<string, { label: string; hue: "tasks" | "approvals" | "danger" | "neutral" }> = {
  connected: { label: "Connected", hue: "tasks" },
  connecting: { label: "Connecting", hue: "approvals" },
  missing_tokens: { label: "Needs tokens", hue: "approvals" },
  error: { label: "Can't connect", hue: "danger" },
  off: { label: "Off", hue: "neutral" },
};

function Section({ title, children, foot, aside }: { title: string; children: ReactNode; foot?: ReactNode; aside?: ReactNode }) {
  return (
    <section className="panel" data-area="agents">
      <div className="panel-head"><span className="swatch" /><h2 className="grow">{title}</h2>{aside}</div>
      <div className="panel-pad stack">{children}</div>
      {foot && <div className="panel-foot">{foot}</div>}
    </section>
  );
}

function useFaqMutation<T>(fn: (v: T) => Promise<Faq | unknown>, done?: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      if (r && typeof r === "object" && "enabled" in (r as object)) qc.setQueryData(["faq"], r);
      else qc.invalidateQueries({ queryKey: ["faq"] });
      if (done) toast(done, "settings");
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(new Error(`Couldn't read ${file.name}.`));
    r.readAsDataURL(file);
  });
}

function DocRow({ d }: { d: FaqDoc }) {
  const [open, setOpen] = useState(false);
  const items = useQuery({
    queryKey: ["faq-doc", d.name, d.updated],
    queryFn: () => api.get<FaqDoc & { items: { id: string; question: string; answer: string; section: string }[] }>(`/api/faq/documents/${encodeURIComponent(d.name)}`),
    enabled: open,
  });
  const del = useFaqMutation(() => api.del(`/api/faq/documents/${encodeURIComponent(d.name)}`), "Document removed");
  return (
    <li data-area={d.error ? "danger" : "settings"} className="flush">
      <div className="faq-doc">
        <button className="faq-q" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <FileText size={16} />
          <span className="grow faq-q-text">{d.name}</span>
          <span className="faint nobreak">
            {d.error ? d.error : d.questions ? plural(d.questions, "question") : plural(d.entries, "passage")}
          </span>
        </button>
        <button className="btn btn-ghost btn-icon btn-danger" aria-label={`Remove ${d.name}`} onClick={() => confirm(`Remove ${d.name}?`) && del.mutate(undefined)}><Trash2 size={16} /></button>
      </div>
      {open && (
        <div className="inset-icon">
          {items.isLoading ? <Loader2 size={15} className="spin" /> : (
            <div className="stack gap-12">
              {(items.data?.items ?? []).map((e) => (
                <div key={e.id}>
                  {e.question ? <strong>{e.question}</strong> : null}
                  {e.section && <span className="faint"> · {e.section}</span>}
                  <div className="muted pre-wrap">{e.answer}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function Documents({ f }: { f: Faq }) {
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    for (const file of Array.from(files)) {
      try {
        const doc = await api.post<FaqDoc>("/api/faq/documents", { name: file.name, content_b64: await readFile(file) });
        toast(`${doc.name}: ${doc.questions ? plural(doc.questions, "question") : plural(doc.entries, "passage")}`, "settings");
      } catch (e) {
        toast((e as Error).message, "danger");
      }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    qc.invalidateQueries({ queryKey: ["faq"] });
  };
  const total = f.documents.reduce((n, d) => n + d.entries, 0);
  return (
    <Section title="FAQ documents" aside={total > 0 && <span className="faint">{plural(total, "entry", "entries")}</span>}>
      {f.documents.length > 0 && <ul className="mailboxes">{f.documents.map((d) => <DocRow key={d.name} d={d} />)}</ul>}
      <div>
        <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => upload(e.target.files)} />
        <button className="btn btn-primary" onClick={() => input.current?.click()} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" /> : <Upload size={15} />} Upload
        </button>
        <span className="faint ml-8">.txt .md .docx .csv .json</span>
      </div>
    </Section>
  );
}

function TryIt({ f }: { f: Faq }) {
  const [q, setQ] = useState("");
  const toast = useToast();
  const ask = useMutation({
    mutationFn: (question: string) => api.post<FaqAnswer>("/api/faq/try", { question }),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const r = ask.data;
  return (
    <Section title="Try it">
      <form className="row nowrap" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask.mutate(q.trim()); }}>
        <input className="input grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="How do I get a refund?" aria-label="Customer question" disabled={!f.documents.length} />
        <button className="btn btn-primary" disabled={!q.trim() || ask.isPending || !f.documents.length}>
          {ask.isPending ? <Loader2 size={15} className="spin" /> : <Send size={15} />} Ask
        </button>
      </form>
      {r && (r.text ? (
        <div className="letter">
          <div className="letter-body">{r.text}</div>
          {r.used.length > 0 && (
            <div className="row gap-8 mt-12">
              {r.used.map((u) => <Chip key={u.id} hue="settings" title={u.source}>{u.question || u.source}</Chip>)}
            </div>
          )}
        </div>
      ) : (
        <>
          <Notice hue="approvals" icon={MessageCircleQuestion} title={r.outcome === "not_a_question" ? "Nothing to answer" : "Not in the FAQ"} />
          {r.redirect && r.outcome !== "not_a_question" && f.slack.say_so && (
            <div className="letter"><div className="letter-body">{f.slack.holding_reply || r.redirect}</div></div>
          )}
        </>
      ))}
    </Section>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  );
}

const list = (s: string) => s.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean);

function Tokens({ f }: { f: Faq }) {
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const toast = useToast();
  const save = useFaqMutation((body: Record<string, string>) => api.put<Faq>("/api/faq/tokens", body), "Tokens saved");
  const copy = async () => {
    try { await navigator.clipboard.writeText(f.manifest); toast("Manifest copied", "settings"); }
    catch { toast("Couldn't copy", "danger"); }
  };
  const openNew = () => (native() ? native()!.open_url(NEW_APP_URL) : window.open(NEW_APP_URL, "_blank", "noopener"));
  const tokenRow = (label: string, set: boolean, value: string, setValue: (v: string) => void, key: string, prefix: string) => (
    <Field label={label}>
      <div className="row nowrap">
        <input className="input grow" type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={set ? "Saved · replace" : `${prefix}…`} aria-label={label} />
        {set && !value && <button className="btn btn-sm" type="button" onClick={() => confirm(`Remove the ${label.toLowerCase()}?`) && save.mutate({ [key]: "" })}>Remove</button>}
      </div>
    </Field>
  );
  return (
    <>
      <div className="row">
        <button className="btn btn-sm" type="button" onClick={copy}><Copy size={15} /> Copy app manifest</button>
        <button className="btn btn-sm" type="button" onClick={openNew}><ExternalLink size={15} /> Create Slack app</button>
      </div>
      <form className="stack" onSubmit={(e) => {
        e.preventDefault();
        const body: Record<string, string> = {};
        if (bot.trim()) body.bot_token = bot.trim();
        if (app.trim()) body.app_token = app.trim();
        if (Object.keys(body).length) save.mutate(body, { onSuccess: () => { setBot(""); setApp(""); } });
      }}>
        <div className="form-grid">
          {tokenRow("Bot token", f.slack.bot_token_set, bot, setBot, "bot_token", "xoxb-")}
          {tokenRow("App token", f.slack.app_token_set, app, setApp, "app_token", "xapp-")}
        </div>
        {(bot.trim() || app.trim()) && <div><button className="btn btn-primary" disabled={save.isPending}>Save tokens</button></div>}
      </form>
    </>
  );
}

/** Which channels Gabe answers in: every one it's in, or the ones ticked here. */
function ChannelPicker({ connected, value, onChange, joined }: {
  connected: boolean; value: string[]; onChange: (v: string[]) => void; joined: Record<string, string>;
}) {
  const [only, setOnly] = useState(value.length > 0);
  const list = useQuery({
    queryKey: ["faq-channels"],
    queryFn: () => api.get<FaqChannel[]>("/api/faq/channels"),
    enabled: connected,
    refetchInterval: 30000,
  });
  const channels = list.data ?? [];
  const byId = new Set(channels.map((c) => c.id));
  // Channels saved by name (or no longer visible) still show, so they can be unticked.
  const extra = value.filter((v) => !byId.has(v) && !channels.some((c) => `#${c.name}` === v || c.name === v));
  const isOn = (c: FaqChannel) => value.includes(c.id) || value.includes(`#${c.name}`) || value.includes(c.name);
  const toggle = (c: FaqChannel) => onChange(isOn(c)
    ? value.filter((v) => v !== c.id && v !== `#${c.name}` && v !== c.name)
    : [...value, c.id]);
  return (
    <div className="field">
      <span>Channels</span>
      <Segmented label="Channels" value={only ? "only" : "all"} onChange={(v) => { setOnly(v === "only"); if (v === "all") onChange([]); }} options={[
        { value: "all", label: "Every channel it's in" },
        { value: "only", label: "Only these" },
      ]} />
      {only && (!connected ? <p className="faint">Connect Slack to pick channels.</p>
        : list.isLoading ? <Loader2 size={15} className="spin" />
        : list.error ? <Notice hue="danger" icon={AlertTriangle}>{(list.error as Error).message}</Notice>
        : (
          <ul className="mailboxes channel-list">
            {channels.map((c) => {
              const state = joined[c.id];
              return (
                <li key={c.id}>
                  <label className="check grow">
                    <input type="checkbox" checked={isOn(c)} onChange={() => toggle(c)} />
                    {c.private ? <Lock size={14} /> : <Hash size={14} />}
                    <span className="faq-q-text">{c.name}</span>
                  </label>
                  {state === "joined" ? <Chip hue="tasks" dot>Joined</Chip>
                    : (state === "needs_invite" || (isOn(c) && !c.member)) ? <Chip hue="approvals" dot title={`Type /invite @Gabe in #${c.name}`}>Invite Gabe</Chip>
                    : c.member ? <span className="faint">In channel</span> : null}
                </li>
              );
            })}
            {extra.map((v) => (
              <li key={v}>
                <label className="check grow">
                  <input type="checkbox" checked onChange={() => onChange(value.filter((x) => x !== v))} />
                  <Hash size={14} /> <span className="faq-q-text">{v.replace(/^#/, "")}</span>
                </label>
                <Chip hue="danger" dot>Not found</Chip>
              </li>
            ))}
            {channels.length === 0 && extra.length === 0 && <li className="faint">No channels.</li>}
          </ul>
        ))}
    </div>
  );
}

function SlackSection({ f }: { f: Faq }) {
  const s = f.slack;
  const [form, setForm] = useState({
    enabled: s.enabled, answer: s.answer, channels: s.channels, staff: s.staff.join(", "),
    direct_messages: s.direct_messages, follow_ups: s.follow_ups, per_user_per_hour: String(s.per_user_per_hour),
    notify_channel: s.notify_channel, create_task: s.create_task, task_owner: s.task_owner, holding_reply: s.holding_reply,
    say_so: s.say_so, where_to_look: f.where_to_look, tone: f.tone, model: f.model,
  });
  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });
  const [joined, setJoined] = useState<Record<string, string>>({});
  const qc = useQueryClient();
  const save = useFaqMutation(async () => {
    const r = await api.put<Faq & { joined?: Record<string, string> }>("/api/faq/settings", {
      tone: form.tone, model: form.model, where_to_look: form.where_to_look,
      slack: { ...form, staff: list(form.staff), per_user_per_hour: Number(form.per_user_per_hour) || 0, tone: undefined, model: undefined, where_to_look: undefined },
    });
    setJoined(r.joined ?? {});
    qc.invalidateQueries({ queryKey: ["faq-channels"] });
    return r;
  }, "Saved");
  const st = STATUS[f.status.state] ?? STATUS.off;
  const rule = DECISION_WORDS[f.reply_rule];
  return (
    <Section title="Slack" aside={<Chip hue={st.hue} dot>{st.label}{f.status.workspace ? ` · ${f.status.workspace}` : ""}</Chip>}
      foot={<button className="btn btn-primary" onClick={() => save.mutate(undefined)} disabled={save.isPending}>{save.isPending && <Loader2 size={15} className="spin" />} Save</button>}>
      {f.status.state === "error" && <Notice hue="danger" icon={AlertTriangle}>{f.status.error}</Notice>}
      <div className="field">
        <span>Bot</span>
        <Segmented label="Slack bot" value={form.enabled ? "on" : "off"} onChange={(v) => set({ enabled: v === "on" })}
          options={[{ value: "on", label: "On" }, { value: "off", label: "Off" }]} />
      </div>
      <Tokens f={f} />
      <div className="field">
        <span>Answers</span>
        <div className="row">
          {rule && <Chip hue={rule.area} dot>{rule.label}</Chip>}
          <NavLink to="/settings/rules" className="btn btn-ghost btn-sm">Approval rules</NavLink>
        </div>
      </div>
      <div className="field">
        <span>Answer</span>
        <Segmented label="Who to answer" value={form.answer} onChange={(v) => set({ answer: v })} options={[
          { value: "everyone", label: "Everyone but staff" },
          { value: "external", label: "Guests and other workspaces" },
        ]} />
      </div>
      <ChannelPicker connected={f.status.state === "connected"} value={form.channels} onChange={(channels) => set({ channels })} joined={joined} />
      <div className="form-grid">
        <Field label="Staff (member IDs)"><input className="input" value={form.staff} onChange={(e) => set({ staff: e.target.value })} placeholder="U0123ABC" /></Field>
        <Field label="Replies per person per hour"><input className="input" type="number" min={0} value={form.per_user_per_hour} onChange={(e) => set({ per_user_per_hour: e.target.value })} /></Field>
        <Field label="Model">
          <select className="select" value={form.model} onChange={(e) => set({ model: e.target.value })}>
            <option value="haiku">Fast (Haiku)</option>
            <option value="sonnet">Balanced (Sonnet)</option>
            <option value="opus">Best (Opus)</option>
          </select>
        </Field>
      </div>
      <div className="row gap-24">
        <Check label="Direct messages" checked={form.direct_messages} onChange={(v) => set({ direct_messages: v })} />
        <Check label="Follow-ups in its threads" checked={form.follow_ups} onChange={(v) => set({ follow_ups: v })} />
      </div>
      <Field label="Tone"><input className="input" value={form.tone} onChange={(e) => set({ tone: e.target.value })} placeholder="Friendly and brief" /></Field>
      <div className="field">
        <span>When the FAQ has no answer</span>
        <Check label="Tell the customer" checked={form.say_so} onChange={(v) => set({ say_so: v })} />
        {form.say_so && (
          <>
            <Field label="Where to look">
              <textarea className="textarea" rows={3} value={form.where_to_look} onChange={(e) => set({ where_to_look: e.target.value })}
                placeholder={"Help centre: https://help.example.com\nEmail support@example.com"} />
            </Field>
            <Field label="Fixed reply"><input className="input" value={form.holding_reply} onChange={(e) => set({ holding_reply: e.target.value })} placeholder="Gabe writes it" /></Field>
          </>
        )}
        <div className="form-grid">
          <Field label="Tell this channel"><input className="input" value={form.notify_channel} onChange={(e) => set({ notify_channel: e.target.value })} placeholder="#support-team" /></Field>
          <Field label="Task owner"><input className="input" value={form.task_owner} onChange={(e) => set({ task_owner: e.target.value })} disabled={!form.create_task} placeholder="Unassigned" /></Field>
        </div>
        <Check label="Add a task" checked={form.create_task} onChange={(v) => set({ create_task: v })} />
      </div>
    </Section>
  );
}

function Questions({ f }: { f: Faq }) {
  const [filter, setFilter] = useState<"all" | "unanswered">("all");
  const [open, setOpen] = useState<string | null>(null);
  const rows = f.questions.filter((q) => filter === "all" || q.outcome === "unanswered");
  const unanswered = f.questions.filter((q) => q.outcome === "unanswered").length;
  return (
    <Section title="Questions" aside={
      <Segmented label="Show" value={filter} onChange={setFilter} options={[
        { value: "all", label: "All", count: f.questions.length },
        { value: "unanswered", label: "No answer", count: unanswered },
      ]} />
    }>
      {rows.length === 0 ? <p className="muted">None yet.</p> : (
        <ul className="mailboxes">
          {rows.map((q: FaqQuestion) => {
            const o = OUTCOMES[q.outcome] ?? { label: q.outcome, hue: "neutral" as const };
            const isOpen = open === q.id;
            return (
              <li key={q.id} className="flush">
                <button className="faq-q" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : q.id)}>
                  <span className="grow">
                    <span className="faq-q-text">{q.text}</span>
                    <span className="faint">{q.user_name} · {q.channel_name} · {when(q.ts)}</span>
                  </span>
                  <Chip hue={o.hue} dot>{o.label}</Chip>
                </button>
                {isOpen && (q.reply || q.error || q.used?.length) && (
                  <div className="inset">
                    {q.reply && <div className="letter"><div className="letter-body">{q.reply}</div></div>}
                    {q.error && <p className="faint">{q.error}</p>}
                    {q.used && q.used.length > 0 && (
                      <div className="row gap-8 mt-8">{q.used.map((u, i) => <Chip key={i} hue="settings">{u}</Chip>)}</div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

export default function FaqBot() {
  const { data, isLoading, error } = useQuery({ queryKey: ["faq"], queryFn: () => api.get<Faq>("/api/faq"), refetchInterval: 15000 });
  const enable = useFaqMutation(() => api.post<Faq>("/api/faq/enable"));
  if (isLoading) return <div className="panel"><SkeletonList rows={4} /></div>;
  if (error) return <Notice hue="danger" icon={AlertTriangle}>{(error as Error).message}</Notice>;
  if (!data?.enabled) {
    return (
      <Section title="FAQ bot">
        <div><button className="btn btn-primary" onClick={() => enable.mutate(undefined)} disabled={enable.isPending}>
          {enable.isPending ? <Loader2 size={15} className="spin" /> : <CheckCircle2 size={15} />} Turn on
        </button></div>
      </Section>
    );
  }
  return (
    <div className="stack">
      <Documents f={data} />
      <TryIt f={data} />
      <SlackSection key={JSON.stringify(data.slack) + data.tone + data.model + data.where_to_look} f={data} />
      <Questions f={data} />
    </div>
  );
}
