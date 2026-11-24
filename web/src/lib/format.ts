export function plural(n: number, one: string, many = one + "s") {
  return `${n} ${n === 1 ? one : many}`;
}

export function dayLabel(iso: string | null | undefined, today: string): string {
  if (!iso) return "No date";
  const d = new Date(iso + "T00:00:00");
  const t = new Date(today + "T00:00:00");
  const diff = Math.round((d.getTime() - t.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  if (diff < 0) return `${-diff} days late`;
  if (diff < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === t.getFullYear() ? undefined : "numeric" });
}

export function longDate(iso: string) {
  return new Date(iso.length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long",
  });
}

export function when(ts: string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  const secs = (Date.now() - d.getTime()) / 1000;
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  const sameDay = new Date().toDateString() === d.toDateString();
  if (sameDay) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ", " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function clock(ts: string) {
  return new Date(ts).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function senderName(from: string | null | undefined) {
  if (!from) return "Unknown sender";
  const m = from.match(/^\s*"?([^"<]+?)"?\s*</);
  return m ? m[1] : from.replace(/[<>]/g, "");
}
