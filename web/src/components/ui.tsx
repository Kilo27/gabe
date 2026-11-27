import DOMPurify from "dompurify";
import { X, type LucideIcon } from "lucide-react";
import { marked } from "marked";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { area as areaInfo, type Area } from "../lib/areas";

type Hue = Area | "danger" | "neutral";

export function PageHeader({ area, title, children, actions }: { area: Area; title?: string; children?: ReactNode; actions?: ReactNode }) {
  const a = areaInfo(area);
  const Icon = a.icon;
  useEffect(() => {
    document.title = `${title ?? a.label} · Gabe`;
  }, [title, a.label]);
  return (
    <header className="page-head" data-area={area}>
      <div className="page-tile" aria-hidden>
        <Icon size={22} strokeWidth={2.1} />
      </div>
      <div className="page-titles">
        <h1>{title ?? a.label}</h1>
        {children && <p>{children}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Chip({ hue, children, solid, dot, title }: { hue: Hue; children: ReactNode; solid?: boolean; dot?: boolean; title?: string }) {
  return (
    <span className={`chip${solid ? " chip-solid" : ""}${dot ? " chip-dot" : ""}`} data-area={hue} title={title}>
      {children}
    </span>
  );
}

export function Empty({ hue, icon: Icon, title, children, action }: { hue: Hue; icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty" data-area={hue}>
      <div className="empty-tile">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Notice({ hue, icon: Icon, title, children, action }: { hue: Hue; icon: LucideIcon; title?: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="notice" data-area={hue} role="status">
      <Icon size={20} />
      <div className="grow">
        {title && <strong>{title}</strong>}
        {children && <div className="muted">{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({ h = 18, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden />;
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <div className="stack pad-16 gap-12" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="stack gap-8">
          <Skeleton h={16} w={`${60 - i * 7}%`} />
          <Skeleton h={12} w={`${85 - i * 5}%`} />
        </div>
      ))}
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text, { async: false }) as string), [text]);
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; count?: number }[]; label: string }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count !== undefined && <span className="count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Sheet({ title, area, onClose, children, footer }: { title: ReactNode; area: Hue; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, textarea, select, button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} data-area={area} ref={ref}>
        <div className="sheet-head">
          <span className="swatch" />
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="sheet-body">{children}</div>
        {footer && <div className="sheet-foot">{footer}</div>}
      </div>
    </>
  );
}

// ---- Toasts -------------------------------------------------------------------

type Toast = { id: number; text: string; hue: Hue };
const ToastCtx = createContext<(text: string, hue?: Hue) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, hue: Hue = "tasks") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, hue }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-wrap" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id} data-area={t.hue}>
            <span className="dot" />
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
