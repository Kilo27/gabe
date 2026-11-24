import { useQuery } from "@tanstack/react-query";
import { CheckSquare, Paperclip, Search, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { api, type Task } from "../api";
import { dayLabel } from "../lib/format";

const OPEN = ["open", "in_progress", "blocked"];

/** Paperclip button that attaches one task from the task list to a request. */
export function TaskPicker({ value, onChange, disabled }: { value: Task | null; onChange: (t: Task | null) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<CSSProperties>({});
  // Fixed position from the button, kept inside the window (opens upward when there's no room below).
  useLayoutEffect(() => {
    if (!open || !ref.current) return;
    const b = ref.current.getBoundingClientRect();
    const w = Math.min(360, window.innerWidth - 24);
    const left = Math.max(12, Math.min(b.left, window.innerWidth - w - 12));
    const below = window.innerHeight - b.bottom;
    setPos(below > 340 ? { left, top: b.bottom + 8, width: w } : { left, bottom: window.innerHeight - b.top + 8, width: w });
  }, [open]);
  const { data } = useQuery({ queryKey: ["tasks"], queryFn: () => api.get<{ today: string; tasks: Task[] }>("/api/tasks"), enabled: open });
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const tasks = (data?.tasks ?? [])
    .filter((t) => OPEN.includes(t.status))
    .filter((t) => !q || `${t.title} ${t.owner} ${t.project}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => (a.due ?? "9999").localeCompare(b.due ?? "9999"));

  return (
    <div className="task-picker" ref={ref}>
      <button type="button" className={`btn btn-icon${value ? " btn-attached" : ""}`} data-area="tasks" disabled={disabled}
        aria-label={value ? `Attached task: ${value.title}. Change` : "Attach a task"} title="Attach a task"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        <Paperclip size={16} />
      </button>
      {open && (
        <div className="task-pop" role="dialog" aria-label="Attach a task" data-area="tasks" style={pos}>
          <div className="search">
            <Search size={15} />
            <input className="input" autoFocus placeholder="Find a task" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <ul>
            {tasks.length === 0 && <li className="faint" style={{ padding: 12 }}>No open tasks{q ? " match" : ""}.</li>}
            {tasks.map((t) => (
              <li key={t.id}>
                <button type="button" aria-pressed={value?.id === t.id} onClick={() => { onChange(t); setOpen(false); setQ(""); }}>
                  <CheckSquare size={15} />
                  <span className="grow">
                    <span className="task-pop-title">{t.title}</span>
                    <span className="faint">{t.owner}{t.due ? ` · ${dayLabel(t.due, data?.today ?? t.due)}` : ""}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** The attached task, shown above the input, with a way to take it off. */
export function AttachedTask({ task, onRemove }: { task: Task; onRemove: () => void }) {
  return (
    <span className="attached-task" data-area="tasks">
      <CheckSquare size={14} />
      <span className="attached-task-title">{task.title}</span>
      <button type="button" aria-label="Remove attached task" onClick={onRemove}><X size={13} /></button>
    </span>
  );
}
