import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ArrowLeft, Clock, Inbox, Mail, RefreshCw, ShieldAlert, Sparkles, Reply, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorState, PageHeader, Pagination, RowsSkeleton, SimulatedBadge } from "@/components/common";
import { Badge, Button, Card, Input, Select, Tabs, TabsList, TabsTrigger, Textarea } from "@/components/ui";
import { useAuth } from "@/contexts";
import { api } from "@/services/api";
import type { Approval, Email, Page } from "@/types";
import { cn, errorMessage, fmtDateTime, timeAgo } from "@/utils";

const CATEGORIES = ["Important", "Work", "Personal", "Promotions", "Finance", "Notifications", "Urgent"];
const CATEGORY_TONE: Record<string, "danger" | "primary" | "warning" | "neutral"> = { Urgent: "danger", Important: "primary", Finance: "warning" };

function Detail({ id, onBack }: { id: string; onBack: () => void }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Approval | null>(null);
  const [instructions, setInstructions] = useState("");
  const [asking, setAsking] = useState(false);
  const { data: email, isPending, isError, error, refetch } = useQuery({ queryKey: ["emails", "detail", id], queryFn: () => api.get<Email>(`/emails/${id}`) });
  useEffect(() => { setDraft(null); setAsking(false); setInstructions(""); }, [id]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["emails"] });
  const analyze = useMutation({ mutationFn: () => api.post<Email>(`/emails/${id}/analyze`), onSuccess: () => { toast.success("Analysis updated"); void refresh(); void queryClient.invalidateQueries({ queryKey: ["dashboard"] }); }, onError: (e) => toast.error(errorMessage(e)) });
  const reply = useMutation({ mutationFn: () => api.post<Approval>(`/emails/${id}/draft-reply`, { instructions: instructions.trim() || null }), onSuccess: (a) => { setDraft(a); setAsking(false); void queryClient.invalidateQueries({ queryKey: ["approvals"] }); }, onError: (e) => toast.error(errorMessage(e)) });
  const archive = useMutation({ mutationFn: (archived: boolean) => api.post(`/emails/${id}/archive`, { archived }), onSuccess: (_d, archived) => { toast.success(archived ? "Archived in AutoPilot. Your mailbox isn't changed." : "Restored"); void refresh(); }, onError: (e) => toast.error(errorMessage(e)) });
  const current = useQuery({ queryKey: ["approvals", "one", draft?.id], queryFn: () => api.get<Approval>(`/approvals/${draft!.id}`), enabled: !!draft });

  if (isPending) return <RowsSkeleton rows={6} />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5">
        <Button variant="ghost" size="sm" className="lg:hidden" onClick={onBack}><ArrowLeft />Back</Button>
        <div className="ml-auto flex gap-1">
          <Button size="sm" onClick={() => analyze.mutate()} loading={analyze.isPending}><Sparkles />{email.analyzed_at ? "Re-analyze" : "Analyze"}</Button>
          <Button size="sm" onClick={() => setAsking(true)} disabled={reply.isPending}><Reply />Draft reply</Button>
          <Button size="sm" variant="ghost" onClick={() => archive.mutate(!email.is_archived)} loading={archive.isPending}>{email.is_archived ? <><ArchiveRestore />Restore</> : <><Archive />Archive</>}</Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <div>
          <h2 className="text-[15px] font-semibold">{email.subject}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{email.sender_name ? `${email.sender_name} <${email.sender_email}>` : email.sender_email} · {fmtDateTime(email.received_at)}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {email.category && <Badge tone={CATEGORY_TONE[email.category]}>{email.category}</Badge>}
            {email.importance === "high" && <Badge tone="danger">High importance</Badge>}
            {email.needs_reply && <Badge tone="warning">Needs reply</Badge>}
            {email.is_demo && <SimulatedBadge label="Demo email" />}
          </div>
        </div>
        {email.injection_flags.length > 0 && (
          <p className="flex gap-1.5 rounded-md bg-danger/10 px-2.5 py-2 text-xs text-danger"><ShieldAlert className="mt-px size-3.5 shrink-0" />This email contains text that tries to instruct an AI. AutoPilot treats it as data and won't act on it.</p>
        )}
        {email.analyzed_at && (
          <Card className="space-y-1.5 bg-surface p-3 text-[13px] shadow-none">
            <p><span className="font-medium">Summary: </span>{email.ai_summary}</p>
            {email.suggested_action && <p><span className="font-medium">Suggested action: </span>{email.suggested_action}</p>}
            {email.deadline_at && <p className="flex items-center gap-1 text-warning"><Clock className="size-3.5" />Deadline {fmtDateTime(email.deadline_at)}{email.deadline_text && ` (“${email.deadline_text}”)`}</p>}
            {email.action_items.length > 0 && <ul className="list-disc pl-4 text-xs text-muted-foreground">{email.action_items.map((a) => <li key={a}>{a}</li>)}</ul>}
          </Card>
        )}
        {asking && (
          <Card className="space-y-2 p-3 shadow-none">
            <label htmlFor="reply-notes" className="block text-xs font-medium">Anything to include? (optional)</label>
            <Textarea id="reply-notes" value={instructions} maxLength={2000} onChange={(e) => setInstructions(e.target.value)} placeholder="e.g. Say I can deliver it by Wednesday" />
            <div className="flex justify-end gap-2"><Button size="sm" onClick={() => setAsking(false)}>Cancel</Button><Button size="sm" variant="primary" onClick={() => reply.mutate()} loading={reply.isPending}><Send />Create draft</Button></div>
          </Card>
        )}
        {draft && current.data && <ApprovalCard approval={current.data} onChanged={() => void current.refetch()} />}
        <div className="whitespace-pre-wrap break-words border-t pt-3 text-[13px] leading-relaxed">{email.body}</div>
      </div>
    </div>
  );
}

export function InboxPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { isDemo } = useAuth();
  const queryClient = useQueryClient();
  const view = params.get("view") ?? "inbox";
  const category = params.get("category") ?? "";
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => { const t = setTimeout(() => { setTerm(q.trim()); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);

  const query = new URLSearchParams({ view, page: String(page), page_size: "20", ...(category && { category }), ...(term && { q: term }) });
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["emails", query.toString()], queryFn: () => api.get<Page<Email>>(`/emails?${query}`) });
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); setParams(next); setPage(1); };
  const refreshAll = () => { for (const k of ["emails", "approvals", "notifications", "activity", "employee", "dashboard"]) void queryClient.invalidateQueries({ queryKey: [k] }); };
  const sync = useMutation({ mutationFn: () => api.post<{ created: number }>("/emails/sync"), onSuccess: (r) => { toast.success(`${r.created} new email(s)`); refreshAll(); }, onError: (e) => toast.error(errorMessage(e)) });
  const simulate = useMutation({ mutationFn: () => api.post("/demo/simulate-email"), onSuccess: () => { toast.success("Simulated email delivered. Matching automations ran."); refreshAll(); }, onError: (e) => toast.error(errorMessage(e)) });

  return (
    <>
      <PageHeader title="Inbox" description="Your email, triaged by your AI employee." actions={isDemo
        ? <Button onClick={() => simulate.mutate()} loading={simulate.isPending}><Mail />Simulate incoming email</Button>
        : <Button onClick={() => sync.mutate()} loading={sync.isPending}><RefreshCw />Fetch latest</Button>} />
      <Card className="overflow-hidden">
        <div className="grid lg:h-[calc(100vh-11.5rem)] lg:min-h-[480px] lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div className={cn("flex min-h-0 flex-col border-r", id && "hidden lg:flex")}>
            <Tabs value={view} onValueChange={(v) => { update("view", v === "inbox" ? "" : v); }}>
              <TabsList className="px-2">
                <TabsTrigger value="inbox">Inbox</TabsTrigger>
                <TabsTrigger value="important">Important</TabsTrigger>
                <TabsTrigger value="needs_reply">Needs reply</TabsTrigger>
                <TabsTrigger value="archived">Archived</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="flex gap-2 border-b p-2">
              <Input aria-label="Search email" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
              <Select aria-label="Filter by category" className="w-36" value={category} onChange={(e) => update("category", e.target.value)}>
                <option value="">All categories</option>
                {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
              </Select>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {isPending ? <RowsSkeleton rows={8} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.items.length === 0 ? <EmptyState icon={Inbox} title="No emails here" description={term || category ? "Try a different search or filter." : "This view is empty."} /> : (
                <ul className="divide-y">{data.items.map((e) => (
                  <li key={e.id}>
                    <button onClick={() => navigate(`/inbox/${e.id}?${params}`)} className={cn("block w-full px-3 py-2.5 text-left transition-colors hover:bg-accent/60", e.id === id && "bg-accent")}>
                      <div className="flex items-center gap-2">
                        {!e.is_read && <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-label="Unread" />}
                        <span className={cn("min-w-0 flex-1 truncate text-[13px]", !e.is_read && "font-semibold")}>{e.sender_name ?? e.sender_email}</span>
                        <span className="shrink-0 text-2xs text-muted-foreground">{timeAgo(e.received_at)}</span>
                      </div>
                      <p className="truncate text-[13px]">{e.subject}</p>
                      <p className="truncate text-xs text-muted-foreground">{e.ai_summary ?? e.snippet}</p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {e.category && <Badge tone={CATEGORY_TONE[e.category]}>{e.category}</Badge>}
                        {e.importance === "high" && <Badge tone="danger">High</Badge>}
                        {e.needs_reply && <Badge tone="warning">Needs reply</Badge>}
                        {e.injection_flags.length > 0 && <Badge tone="danger"><ShieldAlert />Suspicious</Badge>}
                        {!e.analyzed_at && <Badge>Not analyzed</Badge>}
                      </div>
                    </button>
                  </li>
                ))}</ul>
              )}
            </div>
            {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
          </div>
          <div className={cn("min-h-0", !id && "hidden lg:block")}>
            {id ? <Detail id={id} onBack={() => navigate(`/inbox?${params}`)} /> : <EmptyState icon={Mail} title="Select an email" description="Pick a message to see its summary and suggested action." />}
          </div>
        </div>
      </Card>
    </>
  );
}
