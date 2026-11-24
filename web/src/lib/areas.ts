import {
  Activity, CalendarCheck2, CheckSquare, Inbox, MessagesSquare, NotebookPen, Settings, ShieldCheck, Workflow,
  type LucideIcon,
} from "lucide-react";

export type Area = "today" | "ask" | "conversations" | "approvals" | "tasks" | "inbox" | "notes" | "activity" | "settings";

export const AREAS: { id: Area; path: string; label: string; short: string; icon: LucideIcon }[] = [
  { id: "ask", path: "/", label: "Agents", short: "Agents", icon: Workflow },
  { id: "conversations", path: "/ask", label: "Conversations", short: "Chats", icon: MessagesSquare },
  { id: "today", path: "/today", label: "Today", short: "Today", icon: CalendarCheck2 },
  { id: "approvals", path: "/approvals", label: "Approvals", short: "Approve", icon: ShieldCheck },
  { id: "tasks", path: "/tasks", label: "Tasks", short: "Tasks", icon: CheckSquare },
  { id: "inbox", path: "/inbox", label: "Inbox", short: "Inbox", icon: Inbox },
  { id: "notes", path: "/notes", label: "Notes", short: "Notes", icon: NotebookPen },
  { id: "activity", path: "/activity", label: "Activity", short: "Activity", icon: Activity },
  { id: "settings", path: "/settings", label: "Settings", short: "Settings", icon: Settings },
];

export const area = (id: Area) => AREAS.find((a) => a.id === id)!;
