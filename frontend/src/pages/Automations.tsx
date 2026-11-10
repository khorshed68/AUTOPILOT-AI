import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowDown, ArrowUp, FlaskConical, History, Pencil, Play, Plus, Trash2, Workflow, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton, SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Card, ConfirmDialog, Dialog, DialogContent, Field, Input, Select, Switch } from "@/components/ui";
import { api } from "@/services/api";
import type { Automation, AutomationCatalog, AutomationStep, Condition, Execution, Page, SystemStatus } from "@/types";
import { errorMessage, fmtDateTime, timeAgo, titleCase } from "@/utils";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function triggerText(a: Automation): string {
  if (a.trigger_type === "daily") return `Every day at ${a.trigger_config.time}`;
  if (a.trigger_type === "weekly") return `Every ${WEEKDAYS[a.trigger_config.weekday]} at ${a.trigger_config.time}`;
  if (a.trigger_type === "new_email") return "New email received";
  return "Manual";
}

interface Draft { name: string; description: string; trigger_type: Automation["trigger_type"]; trigger_config: Record<string, any>; conditions: Condition[]; condition_logic: "all" | "any"; steps: AutomationStep[] }
const blank: Draft = { name: "", description: "", trigger_type: "manual", trigger_config: {}, conditions: [], condition_logic: "all", steps: [] };

function Builder({ automation, open, onOpenChange, catalog }: { automation: Automation | null; open: boolean; onOpenChange: (o: boolean) => void; catalog: AutomationCatalog }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(blank);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    if (!open) return;
    setProblem("");
    setDraft(automation ? { name: automation.name, description: automation.description, trigger_type: automation.trigger_type, trigger_config: automation.trigger_config, conditions: automation.conditions, condition_logic: automation.condition_logic, steps: automation.steps.map((s) => ({ action_type: s.action_type, config: s.config })) } : blank);
  }, [open, automation]);
  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));
  const isEmail = draft.trigger_type === "new_email";
  const available = catalog.actions.filter((a) => isEmail || a.requires !== "email");
  const setTrigger = (type: Draft["trigger_type"]) => {
    const config = type === "daily" ? { time: "20:00" } : type === "weekly" ? { time: "09:00", weekday: 0 } : {};
    patch({ trigger_type: type, trigger_config: config, conditions: type === "new_email" ? draft.conditions : [], steps: type === "new_email" ? draft.steps : draft.steps.filter((s) => catalog.actions.find((a) => a.type === s.action_type)?.requires !== "email") });
  };
  const moveStep = (index: number, delta: number) => { const steps = [...draft.steps]; const [s] = steps.splice(index, 1); steps.splice(index + delta, 0, s); patch({ steps }); };
  const setCondition = (index: number, change: Partial<Condition>) => patch({ conditions: draft.conditions.map((c, i) => (i === index ? { ...c, ...change } : c)) });
  const kind = (field: string) => catalog.condition_fields.find((f) => f.field === field)?.kind ?? "text";
  const save = useMutation({
    mutationFn: () => (automation ? api.put(`/automations/${automation.id}`, draft) : api.post("/automations", draft)),
    onSuccess: () => { toast.success(automation ? "Automation updated" : "Automation created. Turn it on when you're ready."); void queryClient.invalidateQueries({ queryKey: ["automations"] }); onOpenChange(false); },
    onError: (e) => setProblem(errorMessage(e)),
  });
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.name.trim()) return setProblem("Give the automation a name.");
    if (!draft.steps.length) return setProblem("Add at least one action.");
    save.mutate();
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={automation ? "Edit automation" : "New automation"} className="max-w-xl">
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Name" htmlFor="a-name"><Input id="a-name" value={draft.name} onChange={(e) => patch({ name: e.target.value })} /></Field>
          <Field label="Trigger" hint={catalog.triggers.find((t) => t.type === draft.trigger_type)?.description} htmlFor="a-trigger">
            <Select id="a-trigger" value={draft.trigger_type} onChange={(e) => setTrigger(e.target.value as Draft["trigger_type"])}>{catalog.triggers.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</Select>
          </Field>
          {(draft.trigger_type === "daily" || draft.trigger_type === "weekly") && (
            <div className="grid grid-cols-2 gap-3">
              {draft.trigger_type === "weekly" && <Field label="Day" htmlFor="a-day"><Select id="a-day" value={draft.trigger_config.weekday} onChange={(e) => patch({ trigger_config: { ...draft.trigger_config, weekday: Number(e.target.value) } })}>{WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</Select></Field>}
              <Field label="Time" htmlFor="a-time"><Input id="a-time" type="time" value={draft.trigger_config.time} onChange={(e) => patch({ trigger_config: { ...draft.trigger_config, time: e.target.value } })} /></Field>
            </div>
          )}
          {isEmail && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium">Conditions</p>
                <Button size="sm" variant="ghost" onClick={() => patch({ conditions: [...draft.conditions, { field: "email.has_deadline", op: "is_true" }] })}><Plus />Add</Button>
              </div>
              {draft.conditions.length > 1 && <Select aria-label="Condition logic" value={draft.condition_logic} onChange={(e) => patch({ condition_logic: e.target.value as "all" | "any" })}><option value="all">Match all conditions</option><option value="any">Match any condition</option></Select>}
              {draft.conditions.map((c, i) => (
                <div key={i} className="flex flex-wrap items-center gap-1.5">
                  <Select aria-label="Field" className="w-44" value={c.field} onChange={(e) => setCondition(i, { field: e.target.value, op: kind(e.target.value) === "boolean" ? "is_true" : "contains", value: "" })}>{catalog.condition_fields.map((f) => <option key={f.field} value={f.field}>{f.label}</option>)}</Select>
                  <Select aria-label="Operator" className="w-32" value={c.op} onChange={(e) => setCondition(i, { op: e.target.value })}>{(kind(c.field) === "boolean" ? catalog.boolean_operators : catalog.text_operators).map((o) => <option key={o} value={o}>{titleCase(o)}</option>)}</Select>
                  {kind(c.field) === "text" && <Input aria-label="Value" className="min-w-24 flex-1" value={c.value ?? ""} onChange={(e) => setCondition(i, { value: e.target.value })} />}
                  <Button variant="ghost" size="icon" aria-label="Remove condition" onClick={() => patch({ conditions: draft.conditions.filter((_, x) => x !== i) })}><X /></Button>
                </div>
              ))}
            </div>
          )}
          <div className="space-y-2">
            <p className="text-xs font-medium">Actions</p>
            <ol className="space-y-1">
              {draft.steps.map((s, i) => (
                <li key={i} className="flex items-center gap-2 rounded-md border px-2 py-1">
                  <span className="w-4 text-xs tabular-nums text-muted-foreground">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-[13px]">{catalog.actions.find((a) => a.type === s.action_type)?.label ?? s.action_type}</span>
                  <Button variant="ghost" size="icon" className="size-6" aria-label="Move up" disabled={i === 0} onClick={() => moveStep(i, -1)}><ArrowUp /></Button>
                  <Button variant="ghost" size="icon" className="size-6" aria-label="Move down" disabled={i === draft.steps.length - 1} onClick={() => moveStep(i, 1)}><ArrowDown /></Button>
                  <Button variant="ghost" size="icon" className="size-6" aria-label="Remove action" onClick={() => patch({ steps: draft.steps.filter((_, x) => x !== i) })}><X /></Button>
                </li>
              ))}
            </ol>
            <Select aria-label="Add action" value="" onChange={(e) => e.target.value && patch({ steps: [...draft.steps, { action_type: e.target.value, config: {} }] })}>
              <option value="">Add an action…</option>
              {available.map((a) => <option key={a.type} value={a.type}>{a.label}: {a.description}</option>)}
            </Select>
          </div>
          {problem && <p role="alert" className="text-xs text-danger">{problem}</p>}
          <div className="flex justify-end gap-2"><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" type="submit" loading={save.isPending}>Save</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function History_({ automation, onClose }: { automation: Automation | null; onClose: () => void }) {
  const { data, isPending } = useQuery({ queryKey: ["automations", "executions", automation?.id], queryFn: () => api.get<Page<Execution>>(`/automations/${automation!.id}/executions?page_size=20`), enabled: !!automation, refetchInterval: (q) => (q.state.data?.items.some((e) => e.status === "queued" || e.status === "running") ? 1500 : false) });
  return (
    <Dialog open={!!automation} onOpenChange={(o) => !o && onClose()}>
      {automation && (
        <DialogContent title={`${automation.name}: run history`} className="max-w-xl">
          {isPending ? <RowsSkeleton rows={4} /> : !data?.items.length ? <EmptyState compact icon={History} title="No runs yet" /> : (
            <ul className="space-y-2">
              {data.items.map((e) => (
                <li key={e.id} className="rounded-md border p-2.5">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <StatusBadge status={e.status} />
                    <span className="text-muted-foreground">{fmtDateTime(e.created_at)} · {titleCase(e.trigger)}{e.duration_ms != null && ` · ${(e.duration_ms / 1000).toFixed(1)}s`}</span>
                    {e.is_test && <span className="text-muted-foreground">Test</span>}
                    {e.is_demo && <SimulatedBadge label="Demo" />}
                  </div>
                  {e.error && <p className="mt-1 text-xs text-danger">{e.error}</p>}
                  {e.step_results.length > 0 && <ol className="mt-1.5 space-y-0.5 text-xs">{e.step_results.map((s) => <li key={s.position} className="flex items-center justify-between gap-2"><span>{s.label}{s.error ? `: ${s.error}` : ""}</span><StatusBadge status={s.status} /></li>)}</ol>}
                </li>
              ))}
            </ul>
          )}
        </DialogContent>
      )}
    </Dialog>
  );
}

export function AutomationsPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Automation | null>(null);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [history, setHistory] = useState<Automation | null>(null);
  const [deleting, setDeleting] = useState<Automation | null>(null);
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["automations", "list"], queryFn: () => api.get<Automation[]>("/automations") });
  const catalog = useQuery({ queryKey: ["automations", "catalog"], queryFn: () => api.get<AutomationCatalog>("/automations/catalog"), staleTime: Infinity });
  const system = useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemStatus>("/system/status"), refetchInterval: 60_000 });
  const refresh = () => { for (const k of ["automations", "notifications", "activity", "approvals", "dashboard"]) void queryClient.invalidateQueries({ queryKey: [k] }); };
  const toggle = useMutation({ mutationFn: ({ a, enabled }: { a: Automation; enabled: boolean }) => api.post(`/automations/${a.id}/toggle`, { enabled }), onSuccess: refresh, onError: (e) => toast.error(errorMessage(e)) });
  const run = useMutation({
    mutationFn: ({ a, test }: { a: Automation; test: boolean }) => api.post<Execution>(`/automations/${a.id}/run`, { test }),
    onSuccess: (execution, { a }) => { if (execution.status === "failed") toast.error(execution.error ?? "The run failed."); else toast.success("Run queued. Results will appear in its history."); setHistory(a); refresh(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/automations/${id}`), onSuccess: () => { setDeleting(null); refresh(); }, onError: (e) => toast.error(errorMessage(e)) });

  return (
    <>
      <PageHeader title="Automations" description="Reusable workflows that run in the background. Sensitive steps still ask for approval." actions={<Button variant="primary" onClick={() => { setEditing(null); setBuilderOpen(true); }} disabled={!catalog.data}><Plus />New automation</Button>} />
      {system.data && !system.data.worker_online && <p className="mb-3 flex items-center gap-1.5 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning"><AlertTriangle className="size-3.5" />The background worker isn't reporting in, so scheduled and queued runs won't start until it's back.</p>}
      {isPending ? <Card><RowsSkeleton /></Card> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.length === 0 ? <Card><EmptyState icon={Workflow} title="No automations yet" description="Build a workflow from triggers, conditions and actions." /></Card> : (
        <div className="space-y-3">
          {data.map((a) => (
            <Card key={a.id} className="p-4">
              <div className="flex flex-wrap items-start gap-3">
                <Switch aria-label={`${a.enabled ? "Disable" : "Enable"} ${a.name}`} checked={a.enabled} onCheckedChange={(enabled) => toggle.mutate({ a, enabled })} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-[13px] font-semibold">{a.name}{a.is_demo && <SimulatedBadge label="Demo" />}</p>
                  {a.description && <p className="text-xs text-muted-foreground">{a.description}</p>}
                  <p className="mt-1.5 text-xs"><span className="font-medium">When: </span>{triggerText(a)}{a.conditions.length > 0 && ` · ${a.conditions.length} condition(s), match ${a.condition_logic}`}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{a.steps.map((s) => catalog.data?.actions.find((x) => x.type === s.action_type)?.label ?? s.action_type).join(" → ")}</p>
                  <p className="mt-1 text-2xs text-muted-foreground">{a.enabled && a.next_run_at ? `Next run ${fmtDateTime(a.next_run_at)}` : a.enabled ? "On" : "Off"}{a.last_run_at && ` · Last run ${timeAgo(a.last_run_at)}`}</p>
                </div>
                <div className="flex flex-wrap gap-1">
                  <Button size="sm" onClick={() => run.mutate({ a, test: false })}><Play />Run</Button>
                  <Button size="sm" onClick={() => run.mutate({ a, test: true })}><FlaskConical />Test</Button>
                  <Button size="sm" variant="ghost" onClick={() => setHistory(a)}><History />History</Button>
                  <Button size="icon" variant="ghost" aria-label={`Edit ${a.name}`} onClick={() => { setEditing(a); setBuilderOpen(true); }} disabled={!catalog.data}><Pencil /></Button>
                  <Button size="icon" variant="ghost" aria-label={`Delete ${a.name}`} onClick={() => setDeleting(a)}><Trash2 /></Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
      {catalog.data && <Builder automation={editing} open={builderOpen} onOpenChange={setBuilderOpen} catalog={catalog.data} />}
      <History_ automation={history} onClose={() => setHistory(null)} />
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} title="Delete this automation?" description={`"${deleting?.name}" and its run history will be removed.`} confirmLabel="Delete" loading={remove.isPending} onConfirm={() => deleting && remove.mutate(deleting.id)} />
    </>
  );
}
