import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, MessageCircleQuestion, Monitor, Moon, MoreHorizontal, ShieldCheck, Sun, X } from "lucide-react";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, type Approval, type Notice, type Overview } from "../api";
import { AREAS, type Area } from "../lib/areas";

type Theme = "system" | "light" | "dark";

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const t = localStorage.getItem("gabe-theme");
      return t === "light" || t === "dark" ? t : "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("gabe-theme");
      else localStorage.setItem("gabe-theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);
  return [theme, setTheme];
}

export function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="segmented" role="group" aria-label="Colour theme">
      {([["light", Sun, "Light"], ["system", Monitor, "Auto"], ["dark", Moon, "Dark"]] as const).map(([v, Icon, label]) => (
        <button key={v} type="button" aria-pressed={theme === v} onClick={() => setTheme(v)}>
          <Icon size={14} /> {label}
        </button>
      ))}
    </div>
  );
}

export function useOverview() {
  return useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/api/overview"), refetchInterval: 5000 });
}

function NavItem({ id, onClick, short }: { id: Area; onClick?: () => void; short?: boolean }) {
  const a = AREAS.find((x) => x.id === id)!;
  const { data } = useOverview();
  const count = id === "approvals" ? data?.approvals_pending : id === "tasks" ? data?.tasks.overdue : undefined;
  const Icon = a.icon;
  return (
    <NavLink to={a.path} end={a.path === "/"} data-area={id} onClick={onClick}>
      <span className="nav-tile" aria-hidden>
        <Icon size={short ? 16 : 15} strokeWidth={2.2} />
      </span>
      <span>{short ? a.short : a.label}</span>
      {!!count && (
        <span className="nav-count" aria-label={id === "approvals" ? `${count} waiting` : `${count} overdue`}>
          {count}
        </span>
      )}
    </NavLink>
  );
}

/** Watches for new questions and approvals and announces each one once. */
function NeedsYouWatcher() {
  const nav = useNavigate();
  const seen = useRef<Set<string> | null>(null);
  const [notice, setNotice] = useState<Approval | null>(null);
  const { data } = useQuery({ queryKey: ["approvals"], queryFn: () => api.get<Approval[]>("/api/approvals"), refetchInterval: 2500, refetchIntervalInBackground: true });
  const pending = (data ?? []).filter((a) => a.status === "pending");
  useEffect(() => {
    if (!data) return;
    if (seen.current === null) { seen.current = new Set(pending.map((a) => a.id)); return; }
    const fresh = pending.filter((a) => !seen.current!.has(a.id));
    fresh.forEach((a) => seen.current!.add(a.id));
    if (fresh.length) setNotice(fresh[fresh.length - 1]);
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, "");
    document.title = pending.length ? `(${pending.length}) ${base}` : base;
  });
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 12000);
    return () => clearTimeout(t);
  }, [notice]);
  if (!notice || !pending.some((a) => a.id === notice.id)) return null;  // gone once answered
  const q = notice.kind === "question";
  const who = notice.agent || "Gabe";
  return (
    <div className="needs-banner" role="alert" data-area={q ? "ask" : "approvals"}>
      <span className="needs-banner-icon">{q ? <MessageCircleQuestion size={18} /> : <ShieldCheck size={18} />}</span>
      <div className="grow">
        <strong>{q ? `${who} has a question` : `${who} needs your OK`}</strong>
        <div className="needs-banner-text">{q ? notice.args.question : notice.session_title}</div>
      </div>
      <button className="btn btn-primary btn-sm" onClick={() => { setNotice(null); nav("/approvals"); }}>{q ? "Answer" : "Review"}</button>
      <button className="btn btn-ghost btn-sm btn-icon" aria-label="Dismiss" onClick={() => setNotice(null)}><X size={16} /></button>
    </div>
  );
}

/** Announces each job that finished or failed, once. A failure stays until dismissed. */
function JobWatcher() {
  const nav = useNavigate();
  const loc = useLocation();
  const seen = useRef<Set<string> | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const { data } = useQuery({ queryKey: ["notices"], queryFn: () => api.get<Notice[]>("/api/notices"), refetchInterval: 2500, refetchIntervalInBackground: true });
  useEffect(() => {
    if (!data) return;
    if (seen.current === null) { seen.current = new Set(data.map((n) => n.id)); return; }
    const here = loc.pathname.startsWith("/ask/") ? loc.pathname.slice(5) : null;
    const fresh = data.filter((n) => !seen.current!.has(n.id));
    fresh.forEach((n) => seen.current!.add(n.id));
    const show = fresh.filter((n) => !(n.kind === "done" && n.session_id === here));  // already looking at it
    if (show.length) setNotice(show.find((n) => n.kind === "failed") ?? show[0]);
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!notice || notice.kind === "failed") return;
    const t = setTimeout(() => setNotice(null), 8000);
    return () => clearTimeout(t);
  }, [notice]);
  if (!notice) return null;
  const failed = notice.kind === "failed";
  return (
    <div className="needs-banner" role={failed ? "alert" : "status"} data-area={failed ? "danger" : "tasks"}>
      <span className="needs-banner-icon">{failed ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}</span>
      <div className="grow">
        <strong>{notice.title}</strong>
        {notice.body && <div className="needs-banner-text">{notice.body}</div>}
      </div>
      {notice.session_id && (
        <button className="btn btn-primary btn-sm" onClick={() => { setNotice(null); nav(`/ask/${notice.session_id}`); }}>Open</button>
      )}
      <button className="btn btn-ghost btn-sm btn-icon" aria-label="Dismiss" onClick={() => setNotice(null)}><X size={16} /></button>
    </div>
  );
}

const PRIMARY: Area[] = ["today", "ask", "approvals", "tasks"];

export function Shell({ children }: { children: ReactNode }) {
  useTheme();  // applies the saved theme; the switch is in Settings
  const { data } = useOverview();
  const [more, setMore] = useState(false);
  const loc = useLocation();
  useEffect(() => setMore(false), [loc.pathname]);
  const moreActive = !PRIMARY.some((id) => {
    const p = AREAS.find((a) => a.id === id)!.path;
    return p === "/" ? loc.pathname === "/" : loc.pathname.startsWith(p);
  });

  return (
    <div className="shell">
      <aside className="rail" aria-label="Main">
        <div className="brand">
          <div className="brand-name">{data?.assistant ?? "Gabe"}</div>
          <div className="brand-co">{data?.company ?? "Your chief of staff"}</div>
        </div>
        <nav className="nav">
          {AREAS.map((a, i) => (
            <Fragment key={a.id}>
              {i > 0 && AREAS[i - 1].group !== a.group && <div className="nav-sep" />}
              <NavItem id={a.id} />
            </Fragment>
          ))}
        </nav>
      </aside>

      <main className="main">
        <div className="banners">
          <NeedsYouWatcher />
          <JobWatcher />
        </div>
        <div className="page">{children}</div>
      </main>

      <nav className="tabbar" aria-label="Main">
        {PRIMARY.map((id) => <NavItem key={id} id={id} short />)}
        <button onClick={() => setMore((m) => !m)} aria-expanded={more} className={moreActive ? "active" : ""} style={moreActive ? { color: "var(--rail-ink)" } : undefined}>
          <span className="nav-tile more-tile" aria-hidden>
            <MoreHorizontal size={16} />
          </span>
          More
        </button>
      </nav>
      {more && (
        <>
          <div className="sheet-backdrop" style={{ zIndex: 35 }} onClick={() => setMore(false)} />
          <div className="more-sheet">
            <nav className="nav">
              {AREAS.filter((a) => !PRIMARY.includes(a.id)).map((a) => <NavItem key={a.id} id={a.id} />)}
            </nav>
          </div>
        </>
      )}
    </div>
  );
}
