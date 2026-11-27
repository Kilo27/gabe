import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, MessageCircle, MessageCircleQuestion, Pencil, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Approval } from "../api";
import { when } from "../lib/format";
import { MODELS, MODEL_HINT, modelLabel } from "../lib/agents";
import { RISK, humanize, showValue, tool } from "../lib/tools";
import { Chip, Field, useToast } from "./ui";

const EMAIL_TOOLS = ["send_email", "draft_email"];
const FREEDOM: Record<string, string> = { look_only: "Look, don't touch", ask_first: "Check with me first", standard: "Normal", trusted: "Trusted" };

/** A proposed new bot: who it is, who it reports to, what it may do. */
function NewMemberView({ args }: { args: Record<string, any> }) {
  return (
    <div className="letter">
      <dl>
        <dt>Name</dt><dd className="letter-subject">{args.name}</dd>
        <dt>Job</dt><dd>{args.job}</dd>
        <dt>Reports to</dt><dd>{args.reports_to ? humanize(String(args.reports_to)) : "Whoever asked"}</dd>
        <dt>Freedom</dt><dd>{FREEDOM[args.freedom ?? "standard"] ?? args.freedom}</dd>
        <dt>Model</dt><dd>{modelLabel(args.model)}</dd>
      </dl>
      <div className="letter-body">{args.instructions}</div>
      {args.first_task && <p style={{ marginTop: 12 }}><strong>First task:</strong> {args.first_task}</p>}
      <div className="row" style={{ gap: 6, marginTop: 12 }}>
        {(args.skills ?? []).map((s: string) => <Chip key={s} hue={tool(s).area}>{tool(s).label}</Chip>)}
      </div>
    </div>
  );
}

function NewMemberEditor({ value, onChange }: { value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  const skills: string[] = value.skills ?? [];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <Field label="Name"><input className="input" value={value.name ?? ""} onChange={(e) => onChange({ ...value, name: e.target.value })} /></Field>
      <Field label="Job"><input className="input" value={value.job ?? ""} onChange={(e) => onChange({ ...value, job: e.target.value })} /></Field>
      <Field label="Instructions"><textarea className="textarea" rows={4} value={value.instructions ?? ""} onChange={(e) => onChange({ ...value, instructions: e.target.value })} /></Field>
      <Field label="Freedom">
        <select className="select" value={value.freedom ?? "standard"} onChange={(e) => onChange({ ...value, freedom: e.target.value })}>
          {["look_only", "ask_first", "standard"].map((f) => <option key={f} value={f}>{FREEDOM[f]}</option>)}
        </select>
      </Field>
      <Field label="Model">
        <select className="select" value={value.model ?? ""} onChange={(e) => {
          const { model: _old, ...rest } = value;
          onChange(e.target.value ? { ...rest, model: e.target.value } : rest);
        }}>
          <option value="">Default</option>
          {Object.entries(MODELS).map(([k, label]) => <option key={k} value={k}>{label} · {MODEL_HINT[k]}</option>)}
        </select>
      </Field>
      <div className="field">
        <span>What it can do (tap to remove)</span>
        <div className="day-picks">
          {skills.map((s) => <button key={s} type="button" aria-pressed onClick={() => onChange({ ...value, skills: skills.filter((x) => x !== s) })}>{tool(s).label}</button>)}
        </div>
      </div>
    </div>
  );
}

function EmailView({ args }: { args: Record<string, any> }) {
  return (
    <div className="letter">
      <dl>
        <dt>To</dt>
        <dd>{(args.to ?? []).join(", ")}</dd>
        {args.cc?.length > 0 && (
          <>
            <dt>Copy</dt>
            <dd>{args.cc.join(", ")}</dd>
          </>
        )}
        <dt>Subject</dt>
        <dd className="letter-subject">{args.subject}</dd>
      </dl>
      <div className="letter-body">{args.body}</div>
      {args.reply_to_id && <p className="faint">This is a reply in an existing email thread.</p>}
    </div>
  );
}

/** A drafted answer to a customer's question in Slack. */
function SlackReplyView({ args }: { args: Record<string, any> }) {
  return (
    <div className="letter">
      <dl>
        <dt>Where</dt><dd>{args.channel}</dd>
        <dt>Question</dt><dd className="letter-subject">{args.question}</dd>
      </dl>
      <div className="letter-body">{args.reply}</div>
    </div>
  );
}

function GenericView({ args, name }: { args: Record<string, any>; name: string }) {
  return (
    <dl className="kv">
      {Object.entries(args).map(([k, v]) => (
        <div key={k}>
          <dt>{humanize(k)}</dt>
          <dd>{showValue(name, k, v)}</dd>
        </div>
      ))}
    </dl>
  );
}

const splitList = (s: string) => s.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean);

function Editor({ a, value, onChange }: { a: Approval; value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  if (a.tool === "add_team_member") return <NewMemberEditor value={value} onChange={onChange} />;
  if (a.tool === "reply_on_slack") {
    return (
      <Field label="Answer">
        <textarea className="textarea" rows={6} value={value.reply ?? ""} onChange={(e) => onChange({ ...value, reply: e.target.value })} />
      </Field>
    );
  }
  if (EMAIL_TOOLS.includes(a.tool)) {
    return (
      <div className="stack" style={{ gap: 12 }}>
        <Field label="To">
          <input className="input" value={(value.to ?? []).join(", ")} onChange={(e) => onChange({ ...value, to: splitList(e.target.value) })} />
        </Field>
        <Field label="Copy (optional)">
          <input className="input" value={(value.cc ?? []).join(", ")} onChange={(e) => {
            const cc = splitList(e.target.value);
            const { cc: _old, ...rest } = value;
            onChange(cc.length ? { ...rest, cc } : rest);
          }} />
        </Field>
        <Field label="Subject">
          <input className="input" value={value.subject ?? ""} onChange={(e) => onChange({ ...value, subject: e.target.value })} />
        </Field>
        <Field label="Message">
          <textarea className="textarea" rows={10} value={value.body ?? ""} onChange={(e) => onChange({ ...value, body: e.target.value })} />
        </Field>
      </div>
    );
  }
  return (
    <div className="stack" style={{ gap: 12 }}>
      {Object.entries(value).map(([k, v]) => (
        <Field key={k} label={humanize(k)}>
          {typeof v === "string" && v.length > 60 ? (
            <textarea className="textarea" value={v} onChange={(e) => onChange({ ...value, [k]: e.target.value })} />
          ) : (
            <input className="input" value={Array.isArray(v) ? v.join(", ") : String(v ?? "")} onChange={(e) => onChange({ ...value, [k]: Array.isArray(v) ? splitList(e.target.value) : e.target.value })} />
          )}
        </Field>
      ))}
    </div>
  );
}

/** A question from Gabe or a bot. Quick-answer buttons when it offered choices, or type a reply. */
function QuestionCard({ a, compact }: { a: Approval; compact?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const pending = a.status === "pending";
  const who = a.agent || "Gabe";
  const send = useMutation({
    mutationFn: (answer: string) => api.post<Approval>(`/api/approvals/${a.id}`, { answer }),
    onSuccess: () => {
      toast("Answer sent", "ask");
      setText("");
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["session"] });
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  const choices: string[] = a.args.choices ?? [];
  return (
    <article className={`approval question${compact ? " approval-compact" : ""}`} data-area="ask" data-status={a.status}>
      <header className="approval-head">
        <div className="approval-icon question-icon" aria-hidden><MessageCircleQuestion size={20} /></div>
        <div className="grow">
          <h3>{who} has a question</h3>
          <p className="faint">{when(a.created)}</p>
        </div>
        {pending ? <Chip hue="ask" solid dot>Waiting for you</Chip>
          : a.status === "answered" ? <Chip hue="tasks" dot>Answered</Chip> : <Chip hue="neutral" dot>Expired</Chip>}
      </header>
      <div className="approval-body"><p className="question-text">{a.args.question}</p></div>
      {pending ? (
        <form className="approval-body stack" style={{ gap: 10 }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(text.trim()); }}>
          {choices.length > 0 && (
            <div className="row" style={{ gap: 8 }}>
              {choices.map((c) => (
                <button key={c} type="button" className="btn btn-sm" disabled={send.isPending} onClick={() => send.mutate(c)}>{c}</button>
              ))}
            </div>
          )}
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input className="input grow" value={text} onChange={(e) => setText(e.target.value)} placeholder={choices.length ? "Or type your own answer" : "Type your answer"} aria-label={`Answer ${who}`} />
            <button className="btn btn-primary" disabled={!text.trim() || send.isPending}>Send</button>
          </div>
        </form>
      ) : (
        <div className="approval-why">
          {a.status === "answered" ? <>You answered: “{a.answer}”</> : "The app closed before you answered. Gabe asks again if it still needs to know."}
        </div>
      )}
      {!compact && a.session_id && (
        <footer className="approval-foot">
          <Link to={`/ask/${a.session_id}`} className="btn btn-ghost btn-sm"><MessageCircle size={15} /> Conversation</Link>
        </footer>
      )}
    </article>
  );
}

export function ApprovalCard({ a, compact }: { a: Approval; compact?: boolean }) {
  if (a.kind === "question") return <QuestionCard a={a} compact={compact} />;
  return <ApprovalDecision a={a} compact={compact} />;
}

function ApprovalDecision({ a, compact }: { a: Approval; compact?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [modeState, setMode] = useState<"view" | "edit" | "reject">("view");
  const [draft, setDraft] = useState<Record<string, any>>(a.args);
  const [note, setNote] = useState("");
  const t = tool(a.tool);
  const pending = a.status === "pending";
  const mode = pending ? modeState : "view";

  const decide = useMutation({
    mutationFn: (body: object) => api.post<Approval>(`/api/approvals/${a.id}`, body),
    onSuccess: (r) => {
      toast(r.status === "approved" ? "Approved" : "Turned down", r.status === "approved" ? "tasks" : "approvals");
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      qc.invalidateQueries({ queryKey: ["session"] });
    },
    onError: (e: Error) => toast(e.message, "danger"),
  });

  const risk = RISK[a.risk];
  const statusChip =
    a.status === "approved" ? <Chip hue="tasks" dot>{a.edited ? "Approved after edits" : "Approved"}</Chip>
    : a.status === "rejected" ? <Chip hue="danger" dot>Said no</Chip>
    : a.status === "expired" ? <Chip hue="neutral" dot>Expired</Chip>
    : <Chip hue="approvals" solid dot>Waiting for you</Chip>;

  return (
    <article className={`approval${compact ? " approval-compact" : ""}`} data-area="approvals" data-status={a.status}>
      <header className="approval-head">
        <div className="approval-icon" aria-hidden>
          <ShieldAlert size={20} />
        </div>
        <div className="grow">
          <h3>{a.agent && a.agent !== "Gabe" ? a.agent.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "Gabe"} wants to {t.ask}</h3>
          <p className="faint">
            {when(a.created)}
            {risk && a.risk === "external" && <> · Goes outside the company</>}
          </p>
        </div>
        {statusChip}
      </header>

      {(a.args.why || a.reason) && mode === "view" && (
        <div className="approval-why">
          <strong>Why:</strong> {a.args.why || a.reason}
        </div>
      )}

      <div className="approval-body">
        {mode === "edit" ? (
          <Editor a={a} value={draft} onChange={setDraft} />
        ) : a.tool === "add_team_member" ? (
          <NewMemberView args={a.args} />
        ) : a.tool === "reply_on_slack" ? (
          <SlackReplyView args={a.args} />
        ) : EMAIL_TOOLS.includes(a.tool) ? (
          <EmailView args={a.args} />
        ) : (
          <GenericView args={a.args} name={a.tool} />
        )}
      </div>

      {mode === "reject" && (
        <div className="approval-body" style={{ paddingTop: 0 }}>
          <Field label="Note to Gabe (optional)">
            <textarea className="textarea" rows={3} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
          </Field>
        </div>
      )}

      {!pending && (a.note || a.decided) && (
        <div className="approval-why">
          {a.status === "expired" ? "The app closed before this was answered, so it wasn't done. Gabe asks again if it still needs it." : <>Decided {when(a.decided)}{a.note && <> · Note: “{a.note}”</>}</>}
        </div>
      )}

      <footer className="approval-foot">
        {!compact && a.session_id && (
          <Link to={`/ask/${a.session_id}`} className="btn btn-ghost btn-sm">
            <MessageCircle size={15} /> Conversation
          </Link>
        )}
        <span className="grow" />
        {pending && mode === "view" && (
          <>
            <button className="btn btn-sm" onClick={() => setMode("reject")}>
              <X size={16} /> Say no
            </button>
            <button className="btn btn-sm" onClick={() => setMode("edit")}>
              <Pencil size={15} /> Edit first
            </button>
            <button className="btn btn-go" onClick={() => decide.mutate({ decision: "approve" })} disabled={decide.isPending}>
              {decide.isPending ? <Loader2 size={17} className="spin" /> : <Check size={17} />} Approve
            </button>
          </>
        )}
        {pending && mode === "edit" && (
          <>
            <button className="btn btn-sm btn-ghost" onClick={() => { setDraft(a.args); setMode("view"); }}>Cancel</button>
            <button className="btn btn-go" onClick={() => decide.mutate({ decision: "approve", args: draft })} disabled={decide.isPending}>
              <Check size={17} /> Approve edited
            </button>
          </>
        )}
        {pending && mode === "reject" && (
          <>
            <button className="btn btn-sm btn-ghost" onClick={() => setMode("view")}>Cancel</button>
            <button className="btn btn-primary" onClick={() => decide.mutate({ decision: "reject", note })} disabled={decide.isPending}>
              <X size={17} /> Don't do it
            </button>
          </>
        )}
      </footer>
      {pending && a.live === false && (
        <p className="faint" style={{ padding: "0 18px 14px" }}>Gabe is no longer waiting on this.</p>
      )}
    </article>
  );
}
