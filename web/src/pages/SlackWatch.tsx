import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Copy, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api, native, type SlackWatch } from "../api";
import { Chip, Field, Notice, Segmented, SkeletonList, useToast } from "../components/ui";
import { when } from "../lib/format";
import { DECISION_WORDS } from "../lib/tools";

const NEW_APP_URL = "https://api.slack.com/apps?new_app=1";

function useSlack<T>(fn: (body: T) => Promise<SlackWatch>, label?: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: (d) => { qc.setQueryData(["slack"], d); if (label) toast(label, "settings"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
}

function Status({ d }: { d: SlackWatch }) {
  const st = d.status;
  if (!d.token_set) return <Chip hue="approvals">Needs token</Chip>;
  if (st.state === "error") return <Notice hue="danger" icon={AlertTriangle}>{st.error}</Notice>;
  if (st.state !== "watching") return <Chip hue="neutral">Starting</Chip>;
  return (
    <div className="row" style={{ gap: 8 }}>
      <Chip hue="tasks" dot>Watching</Chip>
      <span className="faint">{st.user} · {st.workspace} · {st.conversations} conversations · checked {when(st.last_scan)}</span>
    </div>
  );
}

function Token({ d }: { d: SlackWatch }) {
  const [value, setValue] = useState("");
  const toast = useToast();
  const save = useSlack((body: { user_token: string }) => api.put<SlackWatch>("/api/slack/token", body), "Token saved");
  const copy = async () => {
    try { await navigator.clipboard.writeText(d.manifest); toast("Manifest copied", "settings"); }
    catch { toast("Couldn't copy", "danger"); }
  };
  const openNew = () => (native() ? native()!.open_url(NEW_APP_URL) : window.open(NEW_APP_URL, "_blank", "noopener"));
  return (
    <>
      <div className="row">
        <button className="btn btn-sm" type="button" onClick={copy}><Copy size={15} /> Copy app manifest</button>
        <button className="btn btn-sm" type="button" onClick={openNew}><ExternalLink size={15} /> Create Slack app</button>
      </div>
      <form className="stack" onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) save.mutate({ user_token: value.trim() }, { onSuccess: () => setValue("") });
      }}>
        <Field label="User token">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input className="input grow" type="password" autoComplete="off" spellCheck={false} value={value}
              onChange={(e) => setValue(e.target.value)} placeholder={d.token_set ? "Saved · replace" : "xoxp-…"} aria-label="User token" />
            {d.token_set && !value && <button className="btn btn-sm" type="button" onClick={() => confirm("Remove the user token?") && save.mutate({ user_token: "" })}>Remove</button>}
            {value.trim() && <button className="btn btn-primary btn-sm" disabled={save.isPending}>Save</button>}
          </div>
        </Field>
      </form>
    </>
  );
}

function Watch({ d }: { d: SlackWatch }) {
  const s = d.settings!;
  const [form, setForm] = useState({ ...s, channels: s.channels.join(", ") });
  const set = (p: Partial<typeof form>) => setForm((f) => ({ ...f, ...p }));
  const save = useSlack((body: unknown) => api.put<SlackWatch>("/api/slack/settings", body), "Saved");
  const scan = useSlack(() => api.post<SlackWatch>("/api/slack/scan"), "Checked");
  const missing = d.status.missing ?? [];
  return (
    <>
      <div className="form-grid">
        <Field label="Direct messages">
          <Segmented label="Direct messages" value={form.direct_messages ? "on" : "off"} onChange={(v) => set({ direct_messages: v === "on" })}
            options={[{ value: "on", label: "Watch" }, { value: "off", label: "Skip" }]} />
        </Field>
        <Field label="Note tasks">
          <Segmented label="Note tasks" value={form.note_tasks ? "on" : "off"} onChange={(v) => set({ note_tasks: v === "on" })}
            options={[{ value: "on", label: "On" }, { value: "off", label: "Off" }]} />
        </Field>
        <Field label="Channels">
          <input className="input" value={form.channels} onChange={(e) => set({ channels: e.target.value })} placeholder="#leadership, #ops" />
        </Field>
        <Field label="Check every (minutes)">
          <input className="input" type="number" min={5} value={form.every_minutes} onChange={(e) => set({ every_minutes: Number(e.target.value) })} />
        </Field>
        <Field label="Task owner">
          <input className="input" value={form.task_owner} onChange={(e) => set({ task_owner: e.target.value })} placeholder={d.status.user || "Your Slack name"} />
        </Field>
        <Field label="Model">
          <Segmented label="Model" value={form.model} onChange={(v) => set({ model: v })}
            options={[{ value: "haiku", label: "Haiku" }, { value: "sonnet", label: "Sonnet" }, { value: "opus", label: "Opus" }]} />
        </Field>
      </div>
      {missing.length > 0 && <Notice hue="approvals" icon={AlertTriangle}>Not a member of {missing.join(", ")}</Notice>}
      {form.note_tasks && d.task_rule !== "allow" && <p className="faint">New tasks: {DECISION_WORDS[d.task_rule]?.label ?? d.task_rule}</p>}
      <div className="row">
        <button className="btn btn-primary" disabled={save.isPending}
          onClick={() => save.mutate({ ...form, channels: form.channels.split(/[,\s]+/).map((x) => x.trim()).filter(Boolean) })}>
          {save.isPending && <Loader2 size={15} className="spin" />} Save
        </button>
        {d.token_set && <button className="btn" disabled={scan.isPending} onClick={() => scan.mutate(undefined)}>
          {scan.isPending ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} Check now
        </button>}
      </div>
    </>
  );
}

export default function SlackWatchPage() {
  const { data: d, isLoading } = useQuery({ queryKey: ["slack"], queryFn: () => api.get<SlackWatch>("/api/slack"), refetchInterval: 20000 });
  const enable = useSlack(() => api.post<SlackWatch>("/api/slack/enable"));
  if (isLoading || !d) return <div className="panel"><SkeletonList rows={4} /></div>;
  if (!d.enabled) {
    return (
      <section className="panel" data-area="settings">
        <div className="panel-head"><span className="swatch" /><h2>Slack</h2></div>
        <div className="panel-pad stack">
          <div><button className="btn btn-primary" onClick={() => enable.mutate(undefined)} disabled={enable.isPending}>Turn on</button></div>
        </div>
      </section>
    );
  }
  return (
    <>
      <section className="panel" data-area="settings">
        <div className="panel-head"><span className="swatch" /><h2>Your Slack</h2></div>
        <div className="panel-pad stack">
          <Status d={d} />
          <Token d={d} />
        </div>
      </section>
      <section className="panel" data-area="settings">
        <div className="panel-head"><span className="swatch" /><h2>What to watch</h2></div>
        <div className="panel-pad stack"><Watch d={d} /></div>
      </section>
      {d.token_set && (
        <section className="panel" data-area="settings">
          <div className="panel-head"><span className="swatch" /><h2>Last 24 hours</h2></div>
          <div className="panel-pad stack">
            {(d.recent ?? []).length === 0 ? <p className="muted">Nothing yet.</p> : (
              <ul className="mailboxes">
                {(d.recent ?? []).map((m) => (
                  <li key={m.ts + m.where} data-area="inbox">
                    {m.mine ? <CheckCircle2 size={15} /> : null}
                    <span className="grow"><strong>{m.who}</strong> <span className="faint">{m.where}</span><br />{m.text.slice(0, 200)}</span>
                    <span className="faint">{when(new Date(Number(m.ts) * 1000).toISOString())}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </>
  );
}
