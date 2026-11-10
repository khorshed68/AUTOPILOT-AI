import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton } from "@/components/common";
import { Badge, Button, Card, CardHeader, ConfirmDialog, Field, Input, Select, Switch, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";
import { useAuth, useTheme } from "@/contexts";
import { api } from "@/services/api";
import type { Memory, Preferences, User } from "@/types";
import { errorMessage, timeAgo, titleCase } from "@/utils";

const TIMEZONES: string[] = (Intl as any).supportedValuesOf?.("timeZone") ?? ["UTC"];

function usePreferences() {
  const { user, setUser } = useAuth();
  return useMutation({
    mutationFn: (changes: Partial<Preferences>) => api.put<Preferences>("/settings/preferences", changes),
    onSuccess: (preferences) => { if (user) setUser({ ...user, preferences } as User); },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div><p className="text-[13px] font-medium">{label}</p>{hint && <p className="text-xs text-muted-foreground">{hint}</p>}</div>
      <Switch aria-label={label} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

function General() {
  const { prefs } = useAuth();
  const { theme, setTheme } = useTheme();
  const save = usePreferences();
  if (!prefs) return null;
  return (
    <Card>
      <CardHeader title="Preferences" />
      <div className="grid gap-4 p-4 sm:grid-cols-2">
        <Field label="Timezone" hint="Used for scheduling and deadlines" htmlFor="s-tz"><Select id="s-tz" value={prefs.timezone} onChange={(e) => save.mutate({ timezone: e.target.value })}>{[...new Set(["UTC", prefs.timezone, ...TIMEZONES])].map((t) => <option key={t}>{t}</option>)}</Select></Field>
        <Field label="Theme" htmlFor="s-theme"><Select id="s-theme" value={theme} onChange={(e) => setTheme(e.target.value as Preferences["theme"])}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></Select></Field>
        <Field label="Communication tone" hint="Used when drafting replies" htmlFor="s-tone"><Select id="s-tone" value={prefs.communication_tone} onChange={(e) => save.mutate({ communication_tone: e.target.value })}>{["professional", "friendly", "concise", "formal"].map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}</Select></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Workday starts" htmlFor="s-start"><Select id="s-start" value={prefs.work_start_hour} onChange={(e) => save.mutate({ work_start_hour: Number(e.target.value) })}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</Select></Field>
          <Field label="Workday ends" htmlFor="s-end"><Select id="s-end" value={prefs.work_end_hour} onChange={(e) => save.mutate({ work_end_hour: Number(e.target.value) })}>{Array.from({ length: 24 }, (_, h) => <option key={h + 1} value={h + 1}>{String(h + 1).padStart(2, "0")}:00</option>)}</Select></Field>
        </div>
      </div>
    </Card>
  );
}

const NOTIFICATION_OPTIONS: [string, string, string][] = [
  ["deadlines", "Upcoming deadlines", "Before a task is due"],
  ["overdue", "Overdue tasks", "At most once a day per task"],
  ["approvals", "Approval requests", "When an action needs your decision"],
  ["automation_failures", "Automation failures", "When a workflow fails"],
  ["daily_summary", "Daily work summaries", "From your summary automation"],
];

function Notifications() {
  const { prefs } = useAuth();
  const save = usePreferences();
  if (!prefs) return null;
  const n = prefs.notification_prefs;
  return (
    <Card>
      <CardHeader title="Notifications" description="In-app notifications" />
      <div className="divide-y">
        {NOTIFICATION_OPTIONS.map(([key, label, hint]) => <Toggle key={key} label={label} hint={hint} checked={n[key] !== false} onChange={(v) => save.mutate({ notification_prefs: { [key]: v } })} />)}
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <p className="text-[13px] font-medium">Deadline warning</p>
          <Select aria-label="Deadline warning" className="w-36" value={Number(n.deadline_lead_hours ?? 24)} onChange={(e) => save.mutate({ notification_prefs: { deadline_lead_hours: Number(e.target.value) } })}>{[6, 12, 24, 48, 72].map((h) => <option key={h} value={h}>{h} hours before</option>)}</Select>
        </div>
      </div>
    </Card>
  );
}

function MemoryRow({ memory, onChange }: { memory: Memory; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(memory.content);
  const save = useMutation({ mutationFn: () => api.put(`/memories/${memory.id}`, { content, kind: memory.kind }), onSuccess: () => { setEditing(false); onChange(); }, onError: (e) => toast.error(errorMessage(e)) });
  const remove = useMutation({ mutationFn: () => api.del(`/memories/${memory.id}`), onSuccess: onChange, onError: (e) => toast.error(errorMessage(e)) });
  return (
    <li className="flex items-center gap-2 px-4 py-2.5">
      {editing ? <Input aria-label="Memory text" value={content} onChange={(e) => setContent(e.target.value)} /> : (
        <div className="min-w-0 flex-1">
          <p className="text-[13px]">{memory.content}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-2xs text-muted-foreground"><Badge>{titleCase(memory.kind)}</Badge>Saved by {memory.source === "ai" ? "AI" : "you"} · {memory.last_used_at ? `last used ${timeAgo(memory.last_used_at)}` : "not used yet"}</p>
        </div>
      )}
      {editing ? <>
        <Button size="icon" variant="ghost" aria-label="Save memory" onClick={() => save.mutate()}><Check /></Button>
        <Button size="icon" variant="ghost" aria-label="Cancel" onClick={() => setEditing(false)}><X /></Button>
      </> : <>
        <Button size="icon" variant="ghost" aria-label="Edit memory" onClick={() => setEditing(true)}><Pencil /></Button>
        <Button size="icon" variant="ghost" aria-label="Delete memory" onClick={() => remove.mutate()}><Trash2 /></Button>
      </>}
    </li>
  );
}

function MemoryTab() {
  const { prefs } = useAuth();
  const queryClient = useQueryClient();
  const save = usePreferences();
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["memories"], queryFn: () => api.get<Memory[]>("/memories") });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["memories"] });
  const add = useMutation({ mutationFn: () => api.post("/memories", { content, kind }), onSuccess: () => { setContent(""); refresh(); }, onError: (e) => toast.error(errorMessage(e)) });
  if (!prefs) return null;
  return (
    <div className="space-y-4">
      <Card className="divide-y">
        <Toggle label="Use memory" hint="Let your AI employee use what's saved here" checked={prefs.memory_enabled} onChange={(v) => save.mutate({ memory_enabled: v })} />
        <Toggle label="Save from conversations" hint="When you state a preference or ask it to remember something" checked={prefs.memory_auto_create} onChange={(v) => save.mutate({ memory_auto_create: v })} />
      </Card>
      <Card>
        <CardHeader title="Saved memories" description="Memory gives context only. It never authorizes an action, and secrets can't be stored." />
        <form className="flex flex-wrap gap-2 border-b p-3" onSubmit={(e) => { e.preventDefault(); if (content.trim()) add.mutate(); }}>
          <Input aria-label="New memory" className="min-w-48 flex-1" placeholder="e.g. Project Atlas is my top priority" value={content} maxLength={1000} onChange={(e) => setContent(e.target.value)} />
          <Select aria-label="Memory type" className="w-32" value={kind} onChange={(e) => setKind(e.target.value)}>{["preference", "project", "tone", "context", "other"].map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}</Select>
          <Button type="submit" disabled={!content.trim()} loading={add.isPending}>Add</Button>
        </form>
        {isPending ? <RowsSkeleton rows={3} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.length === 0 ? <EmptyState compact icon={Brain} title="Nothing saved yet" /> : <ul className="divide-y">{data.map((m) => <MemoryRow key={m.id} memory={m} onChange={refresh} />)}</ul>}
      </Card>
    </div>
  );
}

function AgentsTab() {
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["agents"], queryFn: () => api.get<{ provider: string; model: string; agents: { name: string; display_name: string; description: string; allowed_tools: string[] }[] }>("/agents") });
  if (isPending) return <RowsSkeleton />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  return (
    <Card>
      <CardHeader title="Your AI team" description="Each agent can only use the tools listed. This is enforced by the server." />
      <ul className="divide-y">{data.agents.map((a) => (
        <li key={a.name} className="px-4 py-3">
          <p className="text-[13px] font-medium">{a.display_name}</p>
          <p className="text-xs text-muted-foreground">{a.description}</p>
          {a.allowed_tools.length > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{a.allowed_tools.map((t) => <Badge key={t}>{t}</Badge>)}</div>}
        </li>
      ))}</ul>
    </Card>
  );
}

function DemoTab() {
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<"reset" | "clear" | null>(null);
  const run = useMutation({
    mutationFn: (action: "reset" | "clear") => api.post(`/demo/${action}`),
    onSuccess: (_d, action) => { toast.success(action === "reset" ? "Demo data restored" : "Demo data removed"); setConfirm(null); void queryClient.invalidateQueries(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <CardHeader title="Demo data" description="Sample emails, tasks, events, documents and history, all labelled as simulated." />
      <div className="flex flex-wrap gap-2 p-4">
        <Button onClick={() => setConfirm("reset")}>Reset demo data</Button>
        <Button variant="danger" onClick={() => setConfirm("clear")}>Remove demo data</Button>
      </div>
      <ConfirmDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} title={confirm === "reset" ? "Reset demo data?" : "Remove demo data?"} description="Simulated records and their history are replaced or removed. Records you created yourself are kept." confirmLabel={confirm === "reset" ? "Reset" : "Remove"} loading={run.isPending} onConfirm={() => confirm && run.mutate(confirm)} />
    </Card>
  );
}

export function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <Tabs defaultValue="general">
        <TabsList className="mb-4"><TabsTrigger value="general">General</TabsTrigger><TabsTrigger value="notifications">Notifications</TabsTrigger><TabsTrigger value="memory">Memory</TabsTrigger><TabsTrigger value="agents">Agents</TabsTrigger><TabsTrigger value="demo">Demo data</TabsTrigger></TabsList>
        <TabsContent value="general"><General /></TabsContent>
        <TabsContent value="notifications"><Notifications /></TabsContent>
        <TabsContent value="memory"><MemoryTab /></TabsContent>
        <TabsContent value="agents"><AgentsTab /></TabsContent>
        <TabsContent value="demo"><DemoTab /></TabsContent>
      </Tabs>
    </>
  );
}
