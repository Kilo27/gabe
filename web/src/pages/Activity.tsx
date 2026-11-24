import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Download, History } from "lucide-react";
import { useState } from "react";
import { api, getText, native, type AuditRow } from "../api";
import { useOverview } from "../components/Shell";
import { Chip, Empty, PageHeader, Segmented, SkeletonList, useToast } from "../components/ui";
import { clock } from "../lib/format";
import { agentName, humanize, outcome, tool } from "../lib/tools";

export function ActivityRow({ row, compact }: { row: AuditRow; compact?: boolean }) {
  const { data: ov } = useOverview();
  const [open, setOpen] = useState(false);
  const t = tool(row.tool);
  const o = outcome(row.outcome);
  const input = row.input && typeof row.input === "object" ? Object.entries(row.input) : [];
  return (
    <li className="activity-row" data-area={t.area}>
      <span className="dot" aria-hidden />
      <div className="grow">
        <div className="activity-line">
          <span className="activity-what">{row.outcome === "ok" ? t.past(row.input) : t.label}</span>
          {row.outcome !== "ok" && <Chip hue={o.area} title={o.help}>{o.label}</Chip>}
        </div>
        <div className="faint">
          {clock(row.ts)} · {agentName(row.agent, ov?.assistant)}
        </div>
        {open && (
          <div className="activity-detail">
            {row.outcome !== "ok" && <p className="muted">{o.help}</p>}
            {input.length > 0 && (
              <dl className="kv">
                {input.map(([k, v]) => (
                  <div key={k}>
                    <dt>{humanize(k)}</dt>
                    <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        )}
      </div>
      {!compact && (
        <button className="btn btn-ghost btn-sm btn-icon" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={open ? "Hide details" : "Show details"}>
          <ChevronDown size={17} style={{ rotate: open ? "180deg" : "0deg", transition: "rotate 200ms" }} />
        </button>
      )}
    </li>
  );
}

type Show = "all" | "problem";

export default function Activity() {
  const toast = useToast();
  const { data: ov } = useOverview();
  const [who, setWho] = useState("");
  const [show, setShow] = useState<Show>("all");
  const [what, setWhat] = useState("");
  const [limit, setLimit] = useState(150);
  const params = new URLSearchParams({ limit: String(limit) });
  if (who) params.set("who", who);
  if (show === "problem") params.set("outcome", "problem");
  if (what) params.set("tool", what);
  const { data, isLoading } = useQuery({
    queryKey: ["activity", who, show, what, limit],
    queryFn: () => api.get<{ total: number; rows: AuditRow[]; agents: string[]; tools: string[] }>(`/api/activity?${params}`),
    refetchInterval: 8000,
  });

  const days: [string, AuditRow[]][] = [];
  for (const r of data?.rows ?? []) {
    const d = new Date(r.ts).toDateString();
    const last = days[days.length - 1];
    if (last && last[0] === d) last[1].push(r);
    else days.push([d, [r]]);
  }

  return (
    <>
      <PageHeader area="activity" actions={
        <button className="btn" onClick={async () => {
          const app = native();
          if (!app) { location.href = "/api/activity.csv"; return; }
          try {
            if (await app.save_text("gabe-activity.csv", await getText("/api/activity.csv"))) toast("Exported", "activity");
          } catch (e) { toast((e as Error).message, "danger"); }
        }}>
          <Download size={16} /> Export CSV
        </button>
      } />
      <div className="toolbar">
        <Segmented<Show> label="Show" value={show} onChange={setShow} options={[{ value: "all", label: "Everything" }, { value: "problem", label: "Only problems" }]} />
        <select className="select toolbar-select" value={who} onChange={(e) => setWho(e.target.value)} aria-label="Who did it">
          <option value="">Anyone</option>
          {data?.agents.map((a) => <option key={a} value={a}>{agentName(a, ov?.assistant)}</option>)}
        </select>
        <select className="select toolbar-select" value={what} onChange={(e) => setWhat(e.target.value)} aria-label="What kind of action">
          <option value="">Any action</option>
          {data?.tools.map((t) => <option key={t} value={t}>{tool(t).label}</option>)}
        </select>
      </div>
      <div className="panel">
        {isLoading ? <SkeletonList rows={6} /> : !data?.rows.length ? (
          <Empty hue="activity" icon={History} title={show === "problem" ? "No problems" : "Nothing here yet"}>
          </Empty>
        ) : (
          <>
            {days.map(([d, rows]) => (
              <section key={d}>
                <h2 className="day-head">{d === new Date().toDateString() ? "Today" : new Date(d).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</h2>
                <ul className="activity-list">{rows.map((r, i) => <ActivityRow key={r.ts + i} row={r} />)}</ul>
              </section>
            ))}
            {data.total > data.rows.length && (
              <div style={{ padding: 16, textAlign: "center" }}>
                <button className="btn" onClick={() => setLimit(limit + 200)}>Show older ({data.total - data.rows.length} more)</button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
