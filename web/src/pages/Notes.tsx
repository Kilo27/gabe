import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NotebookPen, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, type Note } from "../api";
import { Chip, Empty, Field, PageHeader, Sheet, SkeletonList, useToast } from "../components/ui";
import { when } from "../lib/format";

function NoteSheet({ note, onClose }: { note: Partial<Note>; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const isNew = !note.id;
  const [topic, setTopic] = useState(note.topic ?? "");
  const [text, setText] = useState(note.note ?? "");
  const [tags, setTags] = useState((note.tags ?? []).join(", "));
  const save = useMutation({
    mutationFn: () => {
      const body = { topic, note: text, tags };
      return isNew ? api.post("/api/notes", body) : api.patch(`/api/notes/${note.id}`, body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notes"] }); toast(isNew ? "Note saved" : "Saved", "notes"); onClose(); },
    onError: (e: Error) => toast(e.message, "danger"),
  });
  return (
    <Sheet title={isNew ? "New note" : "Edit note"} area="notes" onClose={onClose} footer={
      <>
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
      </>
    }>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Topic"><input className="input" value={topic} onChange={(e) => setTopic(e.target.value)} required /></Field>
        <Field label="Note"><textarea className="textarea" rows={6} value={text} onChange={(e) => setText(e.target.value)} required /></Field>
        <Field label="Tags" hint="Comma separated"><input className="input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <button hidden />
      </form>
    </Sheet>
  );
}

export default function Notes() {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Partial<Note> | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["notes", q], queryFn: () => api.get<Note[]>(`/api/notes?q=${encodeURIComponent(q)}`) });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/api/notes/${id}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notes"] }); toast("Note deleted", "notes"); },
    onError: (e: Error) => toast(e.message, "danger"),
  });

  return (
    <>
      <PageHeader area="notes" actions={<button className="btn btn-primary" data-area="notes" onClick={() => setEditing({})}><Plus size={17} /> Add note</button>} />
      <div className="toolbar">
        <div className="search grow search-md">
          <Search size={16} />
          <input className="input" placeholder="Search notes" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search notes" />
        </div>
      </div>
      {isLoading ? <div className="panel"><SkeletonList rows={4} /></div> : !data?.length ? (
        <div className="panel">
          <Empty hue="notes" icon={q ? Search : NotebookPen} title={q ? "No matching notes" : "No notes yet"} />
        </div>
      ) : (
        <div className="notes-grid">
          {data.map((n) => (
            <article key={n.id} className="note" data-area="notes">
              <header className="row">
                <h3 className="grow">{n.topic}</h3>
                <button className="btn btn-ghost btn-sm btn-icon" onClick={() => setEditing(n)} aria-label={`Edit note on ${n.topic}`}><Pencil size={15} /></button>
                <button className="btn btn-ghost btn-sm btn-icon btn-danger" onClick={() => confirm(`Delete the note on “${n.topic}”?`) && del.mutate(n.id)} aria-label={`Delete note on ${n.topic}`}><Trash2 size={15} /></button>
              </header>
              <p>{n.note}</p>
              <footer className="row gap-8">
                {n.tags.map((t) => <Chip key={t} hue="notes">{t}</Chip>)}
                <span className="grow" />
                <span className="faint">{when(n.edited ?? n.created)}</span>
              </footer>
            </article>
          ))}
        </div>
      )}
      {editing && <NoteSheet note={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
