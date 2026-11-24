import { useQuery } from "@tanstack/react-query";
import { Monitor, Moon, MoreHorizontal, Sun } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api, type Overview } from "../api";
import { AREAS, type Area } from "../lib/areas";

type Theme = "system" | "light" | "dark";

function useTheme(): [Theme, (t: Theme) => void] {
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
        <Icon size={short ? 16 : 17} strokeWidth={2.3} />
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

const PRIMARY: Area[] = ["ask", "today", "approvals", "tasks"];

export function Shell({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme();
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
          <div className="brand-mark" aria-hidden>
            <span style={{ background: "var(--today)" }} />
            <span style={{ background: "var(--approvals)" }} />
            <span style={{ background: "var(--tasks)" }} />
            <span style={{ background: "var(--ask)" }} />
          </div>
          <div>
            <div className="brand-name">{data?.assistant ?? "Gabe"}</div>
            <div className="brand-co">{data?.company ? `Chief of staff · ${data.company}` : "Your chief of staff"}</div>
          </div>
        </div>
        <nav className="nav">
          {AREAS.slice(0, 4).map((a) => <NavItem key={a.id} id={a.id} />)}
          <div className="nav-sep" />
          {AREAS.slice(4, 7).map((a) => <NavItem key={a.id} id={a.id} />)}
          <div className="nav-sep" />
          <NavItem id="settings" />
        </nav>
        <div className="rail-foot">
          <div className="theme-switch" role="group" aria-label="Colour theme">
            {([["light", Sun, "Light"], ["system", Monitor, "Auto"], ["dark", Moon, "Dark"]] as const).map(([v, Icon, label]) => (
              <button key={v} aria-pressed={theme === v} onClick={() => setTheme(v)} title={`${label} theme`}>
                <Icon size={14} /> {label}
              </button>
            ))}
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="page">{children}</div>
      </main>

      <nav className="tabbar" aria-label="Main">
        {PRIMARY.map((id) => <NavItem key={id} id={id} short />)}
        <button onClick={() => setMore((m) => !m)} aria-expanded={more} className={moreActive ? "active" : ""} style={moreActive ? { color: "var(--rail-ink)" } : undefined}>
          <span className="nav-tile" style={{ background: "#3a3b40", color: "var(--rail-ink)" }} aria-hidden>
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
              {AREAS.slice(4).map((a) => <NavItem key={a.id} id={a.id} />)}
            </nav>
            <div className="theme-switch" role="group" aria-label="Colour theme" style={{ marginTop: 8 }}>
              {([["light", Sun, "Light"], ["system", Monitor, "Auto"], ["dark", Moon, "Dark"]] as const).map(([v, Icon, label]) => (
                <button key={v} aria-pressed={theme === v} onClick={() => setTheme(v)}>
                  <Icon size={14} /> {label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
