import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { EmptyState, ErrorState, PageHeader, Pagination, PriorityBadge, RowsSkeleton, SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Card, ConfirmDialog, Dialog, DialogContent, Field, Input, Select, Textarea } from "@/components/ui";
import { api } from "@/services/api";
import type { Page, Priority, Task, TaskStatus } from "@/types";
import { cn, errorMessage, fmtDateTime, titleCase, toLocalInput, zonedDate, zonedInputToIso } from "@/utils";

const STATUSES: TaskStatus[] = ["pending", "in_progress", "waiting", "completed", "cancelled"];
const PRIORITIES: Priority[] = ["critical", "high", "medium", "low"];

interface FormState { title: string; description: string; priority: Priority; status: TaskStatus; category: string; progress: number; due: string }
const emptyForm: FormState = { title: "", description: "", priority: "medium", status: "pending", category: "", progress: 0, due: "" };

export function validateTaskForm(form: FormState): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!form.title.trim()) errors.title = "Give the task a title.";
  if (form.title.length > 255) errors.title = "Keep the title under 255 characters.";
  if (form.progress < 0 || form.progress > 100) errors.progress = "Progress must be between 0 and 100.";
  return errors;
}

export function TaskDialog({ task, open, onOpenChange }: { task: Task | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!open) return;
    setErrors({});
    setForm(task ? { title: task.title, description: task.description, priority: task.priority, status: task.status, category: task.category ?? "", progress: task.progress, due: task.due_at ? toLocalInput(zonedDate(task.due_at)) : "" } : emptyForm);
  }, [open, task]);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));
  const save = useMutation({
    mutationFn: () => {
      const body = { title: form.title, description: form.description, priority: form.priority, status: form.status, category: form.category.trim() || null, progress: form.progress, due_at: form.due ? zonedInputToIso(form.due) : null, related_email_id: task?.related_email_id ?? null, related_document_id: task?.related_document_id ?? null };
      return task ? api.put(`/tasks/${task.id}`, body) : api.post("/tasks", body);
    },
    onSuccess: () => { toast.success(task ? "Task updated" : "Task created"); for (const k of ["tasks", "dashboard", "activity"]) void queryClient.invalidateQueries({ queryKey: [k] }); onOpenChange(false); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const found = validateTaskForm(form);
    setErrors(found);
    if (!Object.keys(found).length) save.mutate();
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={task ? "Edit task" : "New task"}>
        <form onSubmit={submit} className="space-y-3" noValidate>
          <Field label="Title" error={errors.title} htmlFor="t-title"><Input id="t-title" autoFocus value={form.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <Field label="Description" htmlFor="t-desc"><Textarea id="t-desc" value={form.description} onChange={(e) => set("description", e.target.value)} /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Priority" htmlFor="t-pri"><Select id="t-pri" value={form.priority} onChange={(e) => set("priority", e.target.value as Priority)}>{PRIORITIES.map((p) => <option key={p} value={p}>{titleCase(p)}</option>)}</Select></Field>
            <Field label="Status" htmlFor="t-status"><Select id="t-status" value={form.status} onChange={(e) => set("status", e.target.value as TaskStatus)}>{STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}</Select></Field>
            <Field label="Due" htmlFor="t-due"><Input id="t-due" type="datetime-local" value={form.due} onChange={(e) => set("due", e.target.value)} /></Field>
            <Field label="Category" htmlFor="t-cat"><Input id="t-cat" value={form.category} maxLength={64} onChange={(e) => set("category", e.target.value)} /></Field>
          </div>
          <Field label={`Progress: ${form.progress}%`} error={errors.progress} htmlFor="t-prog"><input id="t-prog" type="range" min={0} max={100} step={5} value={form.progress} onChange={(e) => set("progress", Number(e.target.value))} className="w-full accent-[hsl(var(--primary))]" /></Field>
          <div className="flex justify-end gap-2 pt-1"><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" type="submit" loading={save.isPending}>{task ? "Save" : "Create task"}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function TasksPage() {
  const [params] = useSearchParams();
  const queryClient = useQueryClient();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [term, setTerm] = useState(q);
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [priority, setPriority] = useState("");
  const [overdue, setOverdue] = useState(params.get("overdue") === "true");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Task | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState<Task | null>(null);
  useEffect(() => { const t = setTimeout(() => { setTerm(q.trim()); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);

  const query = new URLSearchParams({ page: String(page), page_size: "25", ...(status && { status }), ...(priority && { priority }), ...(overdue && { overdue: "true" }), ...(term && { q: term }) });
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["tasks", query.toString()], queryFn: () => api.get<Page<Task>>(`/tasks?${query}`) });
  const refresh = () => { for (const k of ["tasks", "dashboard", "activity"]) void queryClient.invalidateQueries({ queryKey: [k] }); };
  const complete = useMutation({
    mutationFn: (task: Task) => api.put(`/tasks/${task.id}`, { ...task, status: task.status === "completed" ? "pending" : "completed", progress: task.status === "completed" ? 0 : 100 }),
    onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/tasks/${id}`), onSuccess: () => { toast.success("Task deleted"); setDeleting(null); refresh(); }, onError: (e) => toast.error(errorMessage(e)) });

  return (
    <>
      <PageHeader title="Tasks" description="Everything on your plate, from you and your AI employee." actions={<Button variant="primary" onClick={() => { setEditing(null); setDialogOpen(true); }}><Plus />New task</Button>} />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b p-2">
          <Input aria-label="Search tasks" className="w-full sm:w-56" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select aria-label="Status" className="w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option><option value="open">Open</option>{STATUSES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}</Select>
          <Select aria-label="Priority" className="w-36" value={priority} onChange={(e) => { setPriority(e.target.value); setPage(1); }}><option value="">All priorities</option>{PRIORITIES.map((p) => <option key={p} value={p}>{titleCase(p)}</option>)}</Select>
          <label className="flex items-center gap-1.5 px-1 text-[13px]"><input type="checkbox" checked={overdue} onChange={(e) => { setOverdue(e.target.checked); setPage(1); }} className="accent-[hsl(var(--primary))]" />Overdue only</label>
        </div>
        {isPending ? <RowsSkeleton rows={8} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.items.length === 0 ? <EmptyState icon={CheckSquare} title="No tasks found" description="Create one, or ask your AI employee to add it." /> : (
          <ul className="divide-y">
            {data.items.map((t) => (
              <li key={t.id} className="group flex items-start gap-3 px-3 py-2.5">
                <input type="checkbox" aria-label={`Mark ${t.title} complete`} checked={t.status === "completed"} onChange={() => complete.mutate(t)} className="mt-1 accent-[hsl(var(--primary))]" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={cn("text-[13px] font-medium", t.status === "completed" && "text-muted-foreground line-through")}>{t.title}</span>
                    <PriorityBadge priority={t.priority} />
                    <StatusBadge status={t.status} />
                    {t.source === "ai" && <span className="text-2xs text-muted-foreground">by AI</span>}
                    {t.is_demo && <SimulatedBadge label="Demo" />}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                    <span className={cn(t.overdue && "font-medium text-danger")}>{t.due_at ? `${t.overdue ? "Overdue · " : "Due "}${fmtDateTime(t.due_at)}` : "No deadline"}</span>
                    {t.category && <span>{t.category}</span>}
                    {t.status !== "completed" && t.progress > 0 && <span className="flex items-center gap-1"><span className="inline-block h-1 w-12 overflow-hidden rounded bg-muted"><span className="block h-full bg-primary" style={{ width: `${t.progress}%` }} /></span>{t.progress}%</span>}
                  </div>
                </div>
                <div className="flex shrink-0 opacity-100 md:opacity-0 md:focus-within:opacity-100 md:group-hover:opacity-100">
                  <Button variant="ghost" size="icon" aria-label={`Edit ${t.title}`} onClick={() => { setEditing(t); setDialogOpen(true); }}><Pencil /></Button>
                  <Button variant="ghost" size="icon" aria-label={`Delete ${t.title}`} onClick={() => setDeleting(t)}><Trash2 /></Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
      </Card>
      <TaskDialog task={editing} open={dialogOpen} onOpenChange={setDialogOpen} />
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} title="Delete this task?" description={`"${deleting?.title}" will be permanently removed.`} confirmLabel="Delete" loading={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting.id)} />
    </>
  );
}
