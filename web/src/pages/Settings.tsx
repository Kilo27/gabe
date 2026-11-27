import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, ExternalLink, Loader2, Plus, RefreshCw, SquareTerminal, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Navigate, NavLink, useParams } from "react-router-dom";
import { GoogleButton } from "../components/GoogleButton";
import { ThemeSwitch } from "../components/Shell";
import { api, native, type Decision, type Job, type Settings, type Updates } from "../api";
import { Field, Markdown, Notice, PageHeader, Segmented, SkeletonList, useToast } from "../components/ui";
import { DECISION_WORDS, RISK, humanize, tool } from "../lib/tools";

const TABS = [
  ["company", "Company"],
  ["brief", "Daily brief"],
  ["rules", "Approval rules"],
  ["connections", "Connections"],
  ["playbooks", "Playbooks"],
  ["advanced", "Advanced"],
] as const;
type Tab = (typeof TABS)[number][0];
/** Old tab addresses, kept working. */
const MOVED: Record<string, string> = {
  email: "/settings/connections", calendar: "/settings/connections",
  model: "/settings/advanced", customize: "/settings/advanced", updates: "/settings/advanced",
  faq: "/agents/faq",
};
const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];

function useSave(url: string, label = "Saved") {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (body: unknown) => api.put<Settings>(url, body),
    onSuccess: (s) => { qc.setQueryData(["settings"], s); qc.invalidateQueries({ queryKey: ["overview"] }); toast(label, "settings"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
}

function Section({ title, children, foot }: { title: string; children: ReactNode; foot?: ReactNode }) {
  return (
    <section className="panel" data-area="settings">
      <div className="panel-head"><span className="swatch" /><h2>{title}</h2></div>
      <div className="panel-pad stack">{children}</div>
      {foot && <div className="panel-foot">{foot}</div>}
    </section>
  );
}

function SaveBtn({ m, onClick }: { m: { isPending: boolean }; onClick: () => void }) {
  return <button className="btn btn-primary" onClick={onClick} disabled={m.isPending}>{m.isPending && <Loader2 size={15} className="spin" />} Save</button>;
}

function Company({ s }: { s: Settings }) {
  const [f, setF] = useState(s.company);
  const [name, setName] = useState(s.assistant_name);
  // Shown as "Working hours"; saved under the original key (working_hours) unless renamed.
  const [facts, setFacts] = useState(() => Object.entries(s.company.facts).map(([k, v]) => [humanize(k), v, k]));
  const factKey = ([label, , orig]: string[]) => (orig && humanize(orig) === label ? orig : label.trim().toLowerCase().replace(/\s+/g, "_"));
  const save = useSave("/api/settings/company");
  return (
    <Section title="Company" foot={<SaveBtn m={save} onClick={() => save.mutate({ ...f, assistant_name: name, facts: Object.fromEntries(facts.filter((f) => f[0].trim()).map((f) => [factKey(f), f[1]])) })} />}>
      <div className="form-grid">
        <Field label="Company name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Time zone"><input className="input" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} placeholder="Europe/Dublin" /></Field>
        <Field label="Assistant name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      </div>
      <Field label="About the company" hint="People, roles, regular meetings.">
        <textarea className="textarea" rows={5} value={f.profile} onChange={(e) => setF({ ...f, profile: e.target.value })} />
      </Field>
      <div className="field">
        <span>Other facts</span>
        {facts.map(([k, v], i) => (
          <div className="row nowrap" key={i} >
            <input className="input fact-key" value={k} aria-label="Fact name" onChange={(e) => setFacts(facts.map((x, j) => (j === i ? [e.target.value, x[1], x[2]] : x)))} />
            <input className="input grow" value={v} aria-label="Fact value" onChange={(e) => setFacts(facts.map((x, j) => (j === i ? [x[0], e.target.value, x[2]] : x)))} />
            <button className="btn btn-ghost btn-icon btn-danger" aria-label="Remove fact" onClick={() => setFacts(facts.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
          </div>
        ))}
        <div><button className="btn btn-sm" onClick={() => setFacts([...facts, ["", "", ""]])}><Plus size={15} /> Add fact</button></div>
      </div>
    </Section>
  );
}

function Brief({ s }: { s: Settings }) {
  const [time, setTime] = useState(s.briefing.time);
  const [days, setDays] = useState(s.briefing.days);
  const [to, setTo] = useState<string[]>(s.briefing.email_to.length ? s.briefing.email_to : [""]);
  const save = useSave("/api/settings/briefing");
  return (
    <Section title="Daily brief" foot={<SaveBtn m={save} onClick={() => save.mutate({ time, days, email_to: to.map((x) => x.trim()).filter(Boolean) })} />}>
      <div className="form-grid">
        <Field label="Time"><input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} /></Field>
      </div>
      <div className="field">
        <span>Send to</span>
        {to.map((addr, i) => (
          <div className="row nowrap" key={i} >
            <input className="input grow" type={addr.trim().toLowerCase() === "me" ? "text" : "email"} value={addr} aria-label={`Recipient ${i + 1}`}
              placeholder="name@company.com" onChange={(e) => setTo(to.map((x, j) => (j === i ? e.target.value : x)))} />
            <button className="btn btn-ghost btn-icon btn-danger" aria-label="Remove recipient" onClick={() => setTo(to.length > 1 ? to.filter((_, j) => j !== i) : [""])}><Trash2 size={16} /></button>
          </div>
        ))}
        <small>“me” sends to the main connected mailbox.</small>
        <div><button className="btn btn-sm" onClick={() => setTo([...to, ""])}><Plus size={15} /> Add recipient</button></div>
      </div>
      <div className="field">
        <span>Days</span>
        <div className="day-picks">
          {DAYS.map(([d, l]) => (
            <button key={d} type="button" aria-pressed={days.includes(d)} onClick={() => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d])}>{l}</button>
          ))}
        </div>
      </div>
    </Section>
  );
}

function DecisionPick({ value, onChange, inherit }: { value: Decision | ""; onChange: (v: Decision | "") => void; inherit?: Decision }) {
  const opts: { value: Decision | ""; label: ReactNode }[] = (["allow", "ask", "deny"] as Decision[]).map((d) => ({
    value: d,
    label: <><span className="dot" data-area={DECISION_WORDS[d].area} /> {DECISION_WORDS[d].label}</>,
  }));
  if (inherit) opts.unshift({ value: "", label: `Default (${DECISION_WORDS[inherit].label})` });
  return <Segmented label="Rule" value={value} onChange={onChange} options={opts} />;
}

function Rules({ s }: { s: Settings }) {
  const [defaults, setDefaults] = useState(s.policy.defaults);
  const [tools, setTools] = useState<Record<string, Decision>>(() =>
    Object.fromEntries(s.policy.tools.filter((t) => t.source === "tool").map((t) => [t.name, t.decision])),
  );
  const save = useSave("/api/settings/policy");
  return (
    <>
      <Section title="By type of action">
        {(["read", "write", "external"] as const).map((r) => (
          <div className="rule-row" key={r}>
            <div className="grow">
              <strong>{RISK[r].label}</strong>
              <div className="faint">{RISK[r].help}</div>
            </div>
            <DecisionPick value={defaults[r]} onChange={(v) => setDefaults({ ...defaults, [r]: v as Decision })} />
          </div>
        ))}
      </Section>
      <Section title="Specific actions" foot={<SaveBtn m={save} onClick={() => save.mutate({ defaults, tools })} />}>
        {s.policy.tools.map((t) => (
          <div className="rule-row" key={t.name}>
            <div className="grow">
              <strong>{tool(t.name).label}</strong>
            </div>
            <DecisionPick
              value={tools[t.name] ?? ""}
              inherit={defaults[t.risk]}
              onChange={(v) => {
                const next = { ...tools };
                if (v) next[t.name] = v; else delete next[t.name];
                setTools(next);
              }}
            />
          </div>
        ))}
      </Section>
    </>
  );
}

function Email({ s }: { s: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const email = s.integrations.find((i) => i.name === "email");
  const st = email?.status;
  const mode = useSave("/api/settings/email-mode");
  const job = useQuery({
    queryKey: ["job", "gmail"],
    queryFn: () => api.get<Job>("/api/jobs/gmail"),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  useEffect(() => {
    if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["settings"] });
  }, [job.data?.status, qc]);
  const connect = useMutation({
    mutationFn: () => api.post<Job>("/api/settings/gmail/connect"),
    onSuccess: (j) => qc.setQueryData(["job", "gmail"], j),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const disconnect = useMutation({
    mutationFn: (address: string) => api.post("/api/settings/gmail/disconnect", { address }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); toast("Gmail disconnected", "inbox"); },
  });
  const backend = st?.backend ?? email?.settings.backend ?? "outbox";
  return (
    <Section title="Email">
      <div className="field">
        <span>Mode</span>
        <Segmented label="Email mode" value={backend} onChange={(v) => mode.mutate({ backend: v })} options={[
          { value: "gmail", label: "Gmail" },
          { value: "outbox", label: "Practice (nothing is sent)" },
        ]} />
      </div>
      {st && backend !== "gmail" && <Notice hue="inbox" icon={AlertTriangle}>{st.message}</Notice>}
      {st && backend === "gmail" && (
        <div className="field">
          <span>Mailboxes</span>
          {st.state === "missing_file" && <Notice hue="danger" icon={AlertTriangle}>{st.message}</Notice>}
          {(st.mailboxes ?? []).length > 0 && (
            <ul className="mailboxes">
              {(st.mailboxes ?? []).map((m, i) => (
                <li key={m.address || i} data-area="inbox">
                  <CheckCircle2 size={17} />
                  <span className="grow">{m.address || "Connected mailbox"}{i === 0 && (st.mailboxes ?? []).length > 1 && <span className="faint"> · main</span>}</span>
                  {!m.connected && <span className="faint">Needs sign-in</span>}
                  <button className="btn btn-ghost btn-sm" onClick={() => confirm(`Disconnect ${m.address || "this mailbox"}?`) && disconnect.mutate(m.address)}>Disconnect</button>
                </li>
              ))}
            </ul>
          )}
          {st.state !== "missing_file" && (
            <div className="row">
              <GoogleButton onClick={() => connect.mutate()} disabled={job.data?.status === "running"}
                label={(st.mailboxes ?? []).length ? "Add another Google account" : "Sign in with Google"} />
              {job.data?.status === "running" && <span className="faint row gap-8"><Loader2 size={14} className="spin" /> Finish in your browser</span>}
            </div>
          )}
        </div>
      )}
      {job.data?.status === "done" && job.data.result?.address && <p className="faint">Connected {job.data.result.address}.</p>}
      {job.data?.status === "error" && <Notice hue="danger" icon={AlertTriangle}>{job.data.error}</Notice>}
    </Section>
  );
}

function Calendar({ s }: { s: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const st = s.integrations.find((i) => i.name === "calendar")?.status;
  const mode = useSave("/api/settings/calendar-mode");
  const job = useQuery({
    queryKey: ["job", "calendar"],
    queryFn: () => api.get<Job>("/api/jobs/calendar"),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });
  useEffect(() => {
    if (job.data?.status === "done") { qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); }
  }, [job.data?.status, qc]);
  const connect = useMutation({
    mutationFn: () => api.post<Job>("/api/settings/calendar/connect"),
    onSuccess: (j) => qc.setQueryData(["job", "calendar"], j),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const disconnect = useMutation({
    mutationFn: () => api.post("/api/settings/calendar/disconnect"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); toast("Calendar disconnected", "today"); },
  });
  const backend = st?.backend ?? "off";
  return (
    <Section title="Calendar">
      <div className="field">
        <span>Mode</span>
        <Segmented label="Calendar mode" value={backend} onChange={(v) => mode.mutate({ backend: v })} options={[
          { value: "google", label: "Google Calendar" },
          { value: "local", label: "Practice (nobody is invited)" },
          { value: "off", label: "Off" },
        ]} />
      </div>
      {st?.state === "missing_file" && <Notice hue="danger" icon={AlertTriangle}>{st.message}</Notice>}
      {backend === "google" && st?.state === "connected" && (
        <ul className="mailboxes">
          <li data-area="today">
            <CheckCircle2 size={17} />
            <span className="grow">{st.address || "Connected calendar"}</span>
            <button className="btn btn-ghost btn-sm" onClick={() => confirm("Disconnect the calendar?") && disconnect.mutate()}>Disconnect</button>
          </li>
        </ul>
      )}
      {backend === "google" && st?.state === "disconnected" && (
        <div className="row">
          <GoogleButton onClick={() => connect.mutate()} disabled={job.data?.status === "running"} />
          {job.data?.status === "running" && <span className="faint row gap-8"><Loader2 size={14} className="spin" /> Finish in your browser</span>}
        </div>
      )}
      {job.data?.status === "error" && <Notice hue="danger" icon={AlertTriangle}>{job.data.error}</Notice>}
    </Section>
  );
}

function Connections({ s }: { s: Settings }) {
  const servers = s.integrations.find((i) => i.name === "mcp")?.servers ?? [];
  return (
    <Section title="Other services">
      {servers.length === 0 ? <p className="muted">None.</p> : (
        <ul className="mailboxes">
          {servers.map((m) => (
            <li key={m.name} data-area={m.state === "connected" ? "inbox" : "danger"}>
              {m.state === "connected" ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
              <span className="grow">{humanize(m.name)}</span>
              <span className="faint">{m.state === "connected" ? `${m.tools} ${m.tools === 1 ? "action" : "actions"}` : m.error}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

const CLAUDE_CODE_URL = "https://claude.com/claude-code";

function Customize({ s }: { s: Settings }) {
  const toast = useToast();
  const open = useMutation({
    mutationFn: () => api.post("/api/claude-code/open"),
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const get = () => (native() ? native()!.open_url(CLAUDE_CODE_URL) : window.open(CLAUDE_CODE_URL, "_blank", "noopener"));
  return (
    <Section title="Claude Code">
      {s.claude_code.installed ? (
        <div>
          <button className="btn btn-primary" onClick={() => open.mutate()} disabled={open.isPending} title={s.claude_code.folder}>
            <SquareTerminal size={15} /> Open in Claude Code
          </button>
        </div>
      ) : (
        <Notice hue="settings" icon={AlertTriangle} title="Claude Code isn't installed" action={
          <button className="btn btn-sm" onClick={get}><ExternalLink size={15} /> Get Claude Code</button>
        } />
      )}
    </Section>
  );
}

function Playbooks({ s }: { s: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [sel, setSel] = useState<string | null>(s.playbooks[0]?.name ?? null);
  const current = s.playbooks.find((p) => p.name === sel);
  const [name, setName] = useState(current?.name ?? "");
  const [text, setText] = useState(current?.text ?? "");
  const [preview, setPreview] = useState(false);
  useEffect(() => { setName(current?.name ?? ""); setText(current?.text ?? ""); setPreview(false); }, [sel, current?.name, current?.text]);
  const save = useMutation({
    mutationFn: () => api.put(`/api/playbooks/${encodeURIComponent(name)}`, { text, rename_from: sel && sel !== name ? sel : undefined }),
    onSuccess: (r: any) => { qc.invalidateQueries({ queryKey: ["settings"] }); setSel(r.name); toast("Playbook saved", "settings"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const del = useMutation({
    mutationFn: () => api.del(`/api/playbooks/${encodeURIComponent(sel!)}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["settings"] }); setSel(null); toast("Playbook deleted", "settings"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  return (
    <div className="split">
      <nav className="panel sublist" aria-label="Playbooks" data-area="settings">
        <ul>
          {s.playbooks.map((p) => (
            <li key={p.name}><button aria-current={sel === p.name} onClick={() => setSel(p.name)}>{humanize(p.name)}</button></li>
          ))}
        </ul>
        <div className="pad-12"><button className="btn btn-sm w-full" onClick={() => { setSel(null); setName(""); setText(""); }}><Plus size={15} /> New playbook</button></div>
      </nav>
      <Section title={sel ? humanize(sel) : "New playbook"} foot={
        <>
          {sel && <button className="btn btn-ghost btn-danger" onClick={() => confirm("Delete this playbook?") && del.mutate()}><Trash2 size={15} /> Delete</button>}
          <span className="grow" />
          <SaveBtn m={save} onClick={() => save.mutate()} />
        </>
      }>
        <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. client-onboarding" /></Field>
        <div className="field">
          <div className="row between">
            <span>Instructions</span>
            <Segmented label="Mode" value={preview ? "p" : "e"} onChange={(v) => setPreview(v === "p")} options={[{ value: "e", label: "Write" }, { value: "p", label: "Preview" }]} />
          </div>
          {preview ? <div className="panel panel-pad"><Markdown text={text || "_Empty_"} /></div> : (
            <textarea className="textarea" rows={16} value={text} onChange={(e) => setText(e.target.value)} />
          )}
        </div>
      </Section>
    </div>
  );
}

function ApiKey({ s }: { s: Settings }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [key, setKey] = useState("");
  const save = useMutation({
    mutationFn: (k: string) => api.put<{ key_set: boolean }>("/api/settings/api-key", { key: k }),
    onSuccess: (r) => { setKey(""); qc.invalidateQueries({ queryKey: ["settings"] }); toast(r.key_set ? "Key saved" : "Key removed", "settings"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const set = !!s.provider.key_set;
  return (
    <Section title="API key">
      {set && <Notice hue="tasks" icon={CheckCircle2} title={s.desktop ? "Saved in your keychain" : "Set"} action={
        <button className="btn btn-sm" onClick={() => confirm("Remove the API key?") && save.mutate("")}>Remove</button>
      } />}
      <form className="row nowrap" onSubmit={(e) => { e.preventDefault(); if (key.trim()) save.mutate(key.trim()); }}>
        <input className="input grow" type="password" autoComplete="off" spellCheck={false} placeholder={set ? "Replace key" : "Paste your Anthropic API key"} value={key} onChange={(e) => setKey(e.target.value)} aria-label="API key" />
        <button className="btn btn-primary" disabled={!key.trim() || save.isPending}>Save</button>
      </form>
    </Section>
  );
}

function Model({ s }: { s: Settings }) {
  const total = s.usage.reduce((a, d) => ({ input: a.input + d.input, output: a.output + d.output }), { input: 0, output: 0 });
  return (
    <>
      {s.provider.kind === "anthropic" && <ApiKey s={s} />}
      <Section title="Usage (last 30 days)">
        {s.usage.length === 0 ? <p className="muted">No usage recorded yet.</p> : (
          <>
            <p><strong>{(total.input / 1000).toFixed(0)}k</strong> tokens in · <strong>{(total.output / 1000).toFixed(0)}k</strong> tokens out</p>
            <table className="table">
              <thead><tr><th>Day</th><th>In</th><th>Out</th><th>Cached</th></tr></thead>
              <tbody>{s.usage.map((d) => <tr key={d.date}><td>{d.date}</td><td>{d.input.toLocaleString()}</td><td>{d.output.toLocaleString()}</td><td>{d.cached.toLocaleString()}</td></tr>)}</tbody>
            </table>
          </>
        )}
      </Section>
    </>
  );
}

const short = (sha?: string | null) => (sha ? sha.slice(0, 7) : "unknown");
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

function UpdatesTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: u } = useQuery({
    queryKey: ["updates"],
    queryFn: () => api.get<Updates>("/api/updates"),
    refetchInterval: (q) => (q.state.data?.state === "installing" ? 3000 : false),
  });
  const set = (d: Updates) => qc.setQueryData(["updates"], d);
  const onError = (e: Error) => toast(e.message, "danger");
  const check = useMutation({ mutationFn: () => api.post<Updates>("/api/updates/check"), onSuccess: set, onError });
  const install = useMutation({ mutationFn: () => api.post<Updates>("/api/updates/install"), onSuccess: set, onError });
  const auto = useMutation({ mutationFn: (v: boolean) => api.put<Updates>("/api/updates/settings", { auto: v }), onSuccess: set, onError });
  const [token, setToken] = useState("");
  const saveToken = useMutation({
    mutationFn: (t: string) => api.put<Updates>("/api/updates/token", { token: t }),
    onSuccess: (d) => { setToken(""); set(d); toast("Token saved", "settings"); },
    onError,
  });
  useEffect(() => { if (u && !u.checked && u.supported) check.mutate(); }, [u?.checked]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!u) return <div className="panel"><SkeletonList rows={3} /></div>;
  const busy = u.state === "installing" || install.isPending;
  return (
    <>
      <Section title="Version" foot={u.supported && (
        <>
          <button className="btn" onClick={() => check.mutate()} disabled={check.isPending || busy}>
            <RefreshCw size={15} className={check.isPending ? "spin" : undefined} /> Check now
          </button>
          <span className="grow" />
          {(u.state === "available" || busy) && (
            <button className="btn btn-primary" onClick={() => install.mutate()} disabled={busy}>
              {busy ? <Loader2 size={15} className="spin" /> : <Download size={15} />} {busy ? "Installing" : "Update now"}
            </button>
          )}
        </>
      )}>
        <table className="table">
          <tbody>
            <tr><th>Installed</th><td><code>{short(u.installed.commit)}</code> <span className="faint">{when(u.installed.built)}</span></td></tr>
            {u.latest && <tr><th>Latest</th><td><code>{short(u.latest.sha)}</code> {u.latest.message} <span className="faint">{when(u.latest.date)}</span></td></tr>}
            <tr><th>Source</th><td><code>{u.repo}</code> · <code>{u.branch}</code></td></tr>
          </tbody>
        </table>
        {!u.supported && <Notice hue="settings" icon={AlertTriangle}>This copy can't update itself.</Notice>}
        {u.state === "current" && <Notice hue="tasks" icon={CheckCircle2} title="Up to date" />}
        {u.state === "restart" && <Notice hue="tasks" icon={CheckCircle2} title="Restart Gabe to finish" />}
        {u.state === "installing" && <Notice hue="settings" icon={Loader2} title="Installing. Gabe will reopen." />}
        {u.error && <Notice hue="danger" icon={AlertTriangle}>{u.error}</Notice>}
        {u.state === "available" && (u.commits?.length ?? 0) > 0 && (
          <div className="field">
            <span>{u.behind} new</span>
            <ul className="stack gap-4">
              {u.commits!.map((c) => <li key={c.sha}><code className="faint">{short(c.sha)}</code> {c.message}</li>)}
            </ul>
          </div>
        )}
      </Section>
      {u.supported && u.kind !== "source" && (
        <Section title="Automatic updates">
          <Segmented label="Automatic updates" value={u.auto ? "on" : "off"} onChange={(v) => auto.mutate(v === "on")} options={[
            { value: "on", label: "Install when idle" },
            { value: "off", label: "Ask first" },
          ]} />
        </Section>
      )}
      {u.supported && (u.kind === "windows_app" || u.token) && (
        <Section title="GitHub token">
          {u.token && <Notice hue="tasks" icon={CheckCircle2} title={{ keychain: "Saved in your keychain", env: "From the environment", gh: "From the GitHub CLI" }[u.token]} action={
            u.token === "keychain" && <button className="btn btn-sm" onClick={() => confirm("Remove the GitHub token?") && saveToken.mutate("")}>Remove</button>
          } />}
          <form className="row nowrap" onSubmit={(e) => { e.preventDefault(); if (token.trim()) saveToken.mutate(token.trim()); }}>
            <input className="input grow" type="password" autoComplete="off" spellCheck={false} placeholder={u.token ? "Replace token" : "github_pat_…"} value={token} onChange={(e) => setToken(e.target.value)} aria-label="GitHub token" />
            <button className="btn btn-primary" disabled={!token.trim() || saveToken.isPending}>Save</button>
          </form>
        </Section>
      )}
    </>
  );
}

function ConnectionsTab({ s }: { s: Settings }) {
  return <><Email s={s} /><Calendar s={s} /><Connections s={s} /></>;
}

function Advanced({ s }: { s: Settings }) {
  return <><Model s={s} /><Customize s={s} /><UpdatesTab /></>;
}

export default function SettingsPage() {
  const { tab = "company" } = useParams<{ tab: string }>();
  const { data, isLoading, error } = useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/settings") });
  const tabs = useRef<HTMLElement>(null);
  useEffect(() => { tabs.current?.querySelector(".active")?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [tab]);
  if (MOVED[tab]) return <Navigate to={MOVED[tab]} replace />;
  const Body = { company: Company, brief: Brief, rules: Rules, connections: ConnectionsTab, playbooks: Playbooks, advanced: Advanced }[tab as Tab] ?? Company;
  return (
    <>
      <PageHeader area="settings" actions={<ThemeSwitch />} />
      <div className="settings">
        <nav ref={tabs} className="settings-tabs" aria-label="Settings sections" data-area="settings">
          {TABS.map(([id, label]) => <NavLink key={id} to={`/settings/${id}`} className={tab === id ? "active" : undefined}>{label}</NavLink>)}
        </nav>
        <div className="grow">
          {isLoading ? <div className="panel"><SkeletonList rows={4} /></div>
            : error ? <Notice hue="danger" icon={AlertTriangle}>{(error as Error).message}</Notice>
            : data && <Body key={tab + JSON.stringify(data).length} s={data} />}
        </div>
      </div>
    </>
  );
}
