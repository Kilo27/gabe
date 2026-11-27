import {
  Activity, CalendarCheck2, CheckSquare, Inbox, MessageCircle, NotebookPen, Settings, ShieldCheck, Workflow,
  type LucideIcon,
} from "lucide-react";

export type Area = "today" | "ask" | "approvals" | "tasks" | "inbox" | "notes" | "activity" | "agents" | "settings";

/** Nav order. Groups are split by a rule in the rail. */
export const AREAS: { id: Area; path: string; label: string; short: string; icon: LucideIcon; group: 0 | 1 | 2 }[] = [
  { id: "today", path: "/", label: "Today", short: "Today", icon: CalendarCheck2, group: 0 },
  { id: "ask", path: "/ask", label: "Ask Gabe", short: "Ask", icon: MessageCircle, group: 0 },
  { id: "approvals", path: "/approvals", label: "Approvals", short: "Approve", icon: ShieldCheck, group: 0 },
  { id: "tasks", path: "/tasks", label: "Tasks", short: "Tasks", icon: CheckSquare, group: 0 },
  { id: "inbox", path: "/inbox", label: "Inbox", short: "Inbox", icon: Inbox, group: 1 },
  { id: "notes", path: "/notes", label: "Notes", short: "Notes", icon: NotebookPen, group: 1 },
  { id: "activity", path: "/activity", label: "Activity", short: "Activity", icon: Activity, group: 1 },
  { id: "agents", path: "/agents", label: "Agents", short: "Agents", icon: Workflow, group: 2 },
  { id: "settings", path: "/settings", label: "Settings", short: "Settings", icon: Settings, group: 2 },
];

export const area = (id: Area) => AREAS.find((a) => a.id === id)!;
