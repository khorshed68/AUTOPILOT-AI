import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowUp, Bot, Check, CheckCircle2, ChevronDown, CircleSlash, FileBarChart, Loader2, MessageSquarePlus, PanelLeft, RotateCcw, Trash2, Wrench } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApprovalCard } from "@/components/ApprovalCard";
import { ErrorState, Markdown, SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Skeleton } from "@/components/ui";
import { ApiError, api, streamChat, type StreamEvent } from "@/services/api";
import type { Approval, ChatMessage, Conversation, DocumentItem, LedgerEntry, MessageMeta, Page, PlanInfo, StepResult } from "@/types";
import { cn, errorMessage, fmtDateTime, timeAgo, titleCase } from "@/utils";

const SUGGESTIONS = [
  "Summarize my important emails.",
  "Find all emails that require a reply.",
  "Schedule a meeting with the development team next Monday afternoon for one hour.",
  "Prepare a report from my project documents.",
  "Find my overdue tasks.",
  "Give me a summary of today's work.",
];
const AGENT_NAMES: Record<string, string> = { email: "Email Agent", calendar: "Calendar Agent", task: "Task Agent", document: "Document Agent", report: "Report Agent", notification: "Notification Agent" };

interface LiveState {
  userText: string;
  plan?: PlanInfo;
  activeAgent?: string;
  tools: { agent: string; tool: string; ok?: boolean }[];
  steps: StepResult[];
  ledger: LedgerEntry[];
  text: string;
  simulated?: boolean;
}

function StepIcon({ status }: { status?: string }) {
  if (status === "completed") return <CheckCircle2 className="size-3.5 text-success" />;
  if (status === "failed") return <AlertCircle className="size-3.5 text-danger" />;
  if (status === "needs_input") return <AlertCircle className="size-3.5 text-warning" />;
  if (status === "skipped") return <CircleSlash className="size-3.5 text-muted-foreground" />;
  return <Loader2 className="size-3.5 animate-spin text-primary" />;
}

function ExecutionTrace({ plan, steps, tools, running, activeAgent }: { plan?: PlanInfo; steps: StepResult[]; tools?: LiveState["tools"]; running?: boolean; activeAgent?: string }) {
  const [open, setOpen] = useState(!!running);
  useEffect(() => { if (running) setOpen(true); }, [running]);
  if (!plan?.steps.length) return null;
  const byId = new Map(steps.map((s) => [s.task_id, s]));
  return (
    <div className="rounded-md border bg-surface text-xs">
      <button aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left font-medium">
        {running ? <Loader2 className="size-3.5 animate-spin text-primary" /> : <Check className="size-3.5 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate">{plan.objective}</span>
        <span className="shrink-0 font-normal text-muted-foreground">{plan.steps.length} step{plan.steps.length === 1 ? "" : "s"}</span>
        <ChevronDown className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="space-y-1.5 border-t px-2.5 py-2">
          {plan.steps.map((step) => {
            const result = byId.get(step.id);
            const active = running && !result && activeAgent === step.agent;
            const queued = running && !result && !active;
            const agentTools = (tools ?? []).filter((t) => t.agent === step.agent);
            return (
              <li key={step.id}>
                <div className="flex items-start gap-2">
                  <span className="mt-px">{queued ? <span className="block size-3.5 rounded-full border" /> : <StepIcon status={result?.status} />}</span>
                  <div className="min-w-0 flex-1">
                    <span className="font-medium">{AGENT_NAMES[step.agent] ?? step.agent}</span>
                    <span className="text-muted-foreground"> · {step.goal}</span>
                    {result && result.duration_ms > 0 && <span className="text-muted-foreground"> · {(result.duration_ms / 1000).toFixed(1)}s</span>}
                    {result?.errors.map((e) => <p key={e} className="text-danger">{e}</p>)}
                    {active && agentTools.length > 0 && <p className="mt-0.5 flex items-center gap-1 text-muted-foreground"><Wrench className="size-3" />{titleCase(agentTools[agentTools.length - 1].tool)}</p>}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function Ledger({ ledger }: { ledger: LedgerEntry[] }) {
  const done = ledger.filter((e) => e.kind === "done");
  if (!done.length) return null;
  return (
    <div className="rounded-md border px-2.5 py-2 text-xs">
      <p className="mb-1 font-medium text-muted-foreground">Confirmed actions</p>
      <ul className="space-y-0.5">{done.map((e, i) => <li key={i} className="flex items-center gap-1.5"><Check className="size-3 text-success" />{e.title}</li>)}</ul>
    </div>
  );
}

function DocumentPreview({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["documents", "detail", id], queryFn: () => api.get<DocumentItem>(`/documents/${id}`) });
  if (!data) return null;
  return (
    <div className="overflow-hidden rounded-md border" data-testid="document-preview">
      <div className="flex items-center gap-2 border-b bg-surface px-2.5 py-1.5 text-xs">
        <FileBarChart className="size-3.5 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate font-medium">{data.title}</span>
        <Link to={`/documents/${id}`} className="shrink-0 text-primary hover:underline">Open and edit</Link>
      </div>
      <Markdown className="max-h-52 overflow-y-auto p-3 text-[13px]">{data.content ?? ""}</Markdown>
    </div>
  );
}

function AssistantMessage({ message, approvals, onRetry, retrying }: { message: ChatMessage; approvals: Approval[]; onRetry: () => void; retrying: boolean }) {
  const meta: MessageMeta = message.meta ?? {};
  const cards = approvals.filter((a) => meta.approval_ids?.includes(a.id));
  return (
    <div className="flex gap-3">
      <Avatar />
      <div className="min-w-0 flex-1 space-y-2">
        <Meta time={message.created_at} simulated={meta.simulated_ai} />
        <ExecutionTrace plan={meta.plan} steps={meta.steps ?? []} />
        {message.status === "error" ? (
          <div role="alert" className="rounded-md border border-danger/30 bg-danger/5 p-3 text-[13px]">
            <p className="flex items-center gap-1.5 font-medium text-danger"><AlertCircle className="size-3.5" />That request didn't finish</p>
            <p className="mt-1">{message.content}</p>
            {meta.error?.retryable && <Button className="mt-2" size="sm" onClick={onRetry} loading={retrying}><RotateCcw />Retry</Button>}
          </div>
        ) : <Markdown>{message.content}</Markdown>}
        <Ledger ledger={meta.ledger ?? []} />
        {meta.ledger?.filter((e) => e.entity === "document" && e.entity_id).map((e) => <DocumentPreview key={e.entity_id} id={e.entity_id!} />)}
        {meta.memories_saved?.map((m) => <p key={m} className="text-xs text-muted-foreground">Saved to memory: {m}</p>)}
        {cards.length > 0 && <div className="space-y-2 pt-1">{cards.map((a) => <ApprovalCard key={a.id} approval={a} />)}</div>}
      </div>
    </div>
  );
}

const Avatar = () => <div className="flex size-7 shrink-0 items-center justify-center rounded-md border bg-surface text-muted-foreground"><Bot className="size-4" /></div>;
const Meta = ({ time, simulated }: { time?: string; simulated?: boolean }) => (
  <div className="flex h-5 items-center gap-2 text-xs text-muted-foreground">
    <span className="font-medium text-foreground">AutoPilot</span>
    {time && <time dateTime={time} title={fmtDateTime(time)}>{timeAgo(time)}</time>}
    {simulated && <SimulatedBadge label="Demo AI" />}
  </div>
);

function UserMessage({ text, time }: { text: string; time?: string }) {
  return (
    <div className="flex flex-col items-end gap-0.5">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-accent px-3 py-2 text-[14px]">{text}</div>
      {time && <time className="pr-1 text-2xs text-muted-foreground" dateTime={time} title={fmtDateTime(time)}>{timeAgo(time)}</time>}
    </div>
  );
}

export function AIEmployeePage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [live, setLive] = useState<LiveState | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const conversations = useQuery({ queryKey: ["conversations"], queryFn: () => api.get<Conversation[]>("/ai/conversations") });
  const detail = useQuery({ queryKey: ["conversation", id], queryFn: () => api.get<Conversation & { messages: ChatMessage[] }>(`/ai/conversations/${id}`), enabled: !!id && !live });
  const approvals = useQuery({ queryKey: ["approvals", "conversation", id], queryFn: () => api.get<Page<Approval>>(`/approvals?conversation_id=${id}&page_size=100`), enabled: !!id });

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [detail.data?.messages.length, live?.text, live?.steps.length, live?.plan, approvals.data]);

  const finishRun = useCallback(async (conversationId: string | undefined) => {
    await Promise.all(["conversations", "employee", "tasks", "dashboard", "activity", "notifications", "approvals"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
    if (conversationId) await queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
    setLive(null);
  }, [queryClient]);

  const handleEvents = useCallback((setConversation: (id: string) => void) => (event: StreamEvent) => {
    setLive((state) => {
      if (!state) return state;
      switch (event.type) {
        case "run_started": return { ...state, simulated: event.simulated_ai };
        case "plan": return { ...state, plan: event.plan };
        case "agent_started": return { ...state, activeAgent: event.agent };
        case "tool_call": return { ...state, tools: [...state.tools, { agent: event.agent, tool: event.tool }] };
        case "agent_finished": return { ...state, steps: [...state.steps, event.result] };
        case "action": return { ...state, ledger: [...state.ledger, event.action] };
        case "token": return { ...state, text: state.text + event.text };
        default: return state;
      }
    });
    if (event.type === "accepted") setConversation(event.conversation_id);
  }, []);

  const run = useCallback(async (text: string, runner: (onEvent: (e: StreamEvent) => void) => Promise<void>, startConversation?: string) => {
    setLive({ userText: text, tools: [], steps: [], ledger: [], text: "" });
    let conversationId = startConversation;
    try {
      await runner(handleEvents((cid) => {
        conversationId = cid;
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }));
      if (conversationId && conversationId !== id) navigate(`/ai/${conversationId}`, { replace: true });
    } catch (error) {
      toast.error(errorMessage(error));
      if (!(error instanceof ApiError && error.status >= 400 && error.status < 500)) setInput((v) => v || text);
    } finally {
      await finishRun(conversationId);
    }
  }, [finishRun, handleEvents, id, navigate, queryClient]);

  const send = (text: string) => {
    const message = text.trim();
    if (!message || live) return;
    setInput("");
    void run(message, (onEvent) => (id ? streamChat(`/ai/conversations/${id}/messages`, { message, stream: true }, onEvent) : streamChat("/ai/chat", { message, stream: true }, onEvent)), id);
  };

  const retry = (message: ChatMessage, userText: string) => {
    void run(userText, (onEvent) => streamChat(`/ai/conversations/${message.conversation_id}/messages/${message.id}/retry`, undefined, onEvent), message.conversation_id);
  };

  const remove = async (conversation: Conversation) => {
    try {
      await api.del(`/ai/conversations/${conversation.id}`);
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (conversation.id === id) navigate("/ai");
    } catch (error) { toast.error(errorMessage(error)); }
  };

  // A prompt handed over from another page (e.g. a dashboard recommendation) runs exactly once.
  const handedOver = (location.state as { prompt?: string } | null)?.prompt;
  const consumedPrompt = useRef<string | null>(null);
  useEffect(() => {
    if (!handedOver || consumedPrompt.current === handedOver) return;
    consumedPrompt.current = handedOver;
    navigate(location.pathname, { replace: true, state: null });
    send(handedOver);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handedOver]);

  const messages = useMemo(() => detail.data?.messages ?? [], [detail.data]);
  const list = approvals.data?.items ?? [];
  const showEmpty = !id && !live;

  return (
    <div className="flex h-full">
      <aside className={cn("absolute z-20 h-[calc(100%-3rem)] w-64 shrink-0 flex-col border-r bg-surface md:static md:flex md:h-full", listOpen ? "flex" : "hidden")} aria-label="Conversations">
        <div className="p-2"><Button className="w-full justify-start" onClick={() => { navigate("/ai"); setListOpen(false); }}><MessageSquarePlus />New conversation</Button></div>
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {conversations.isPending && <><Skeleton className="h-7" /><Skeleton className="mt-1 h-7" /></>}
          {conversations.data?.length === 0 && <p className="px-2 py-3 text-xs text-muted-foreground">Your conversations will appear here.</p>}
          {conversations.data?.map((c) => (
            <div key={c.id} className={cn("group flex items-center rounded-md hover:bg-accent", c.id === id && "bg-accent")}>
              <button className="min-w-0 flex-1 truncate px-2 py-1.5 text-left text-[13px]" onClick={() => { navigate(`/ai/${c.id}`); setListOpen(false); }}>{c.title}</button>
              <button aria-label={`Delete ${c.title}`} className="mr-1 rounded p-1 text-muted-foreground opacity-0 hover:text-danger focus:opacity-100 group-hover:opacity-100" onClick={() => void remove(c)}><Trash2 className="size-3.5" /></button>
            </div>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center border-b px-2 py-1 md:hidden"><Button variant="ghost" size="sm" onClick={() => setListOpen(!listOpen)}><PanelLeft />Conversations</Button></div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {showEmpty && (
              <div className="pt-10 md:pt-20">
                <h1 className="text-xl font-semibold tracking-tight">What should I take care of?</h1>
                <p className="mt-1 text-[13px] text-muted-foreground">Describe the outcome you want. Anything sensitive waits for your approval.</p>
                <div className="mt-5 grid gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} className="rounded-md border px-3 py-2 text-left text-[13px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">{s}</button>)}
                </div>
              </div>
            )}
            {id && detail.isPending && !live && <div className="space-y-3"><Skeleton className="ml-auto h-9 w-1/2" /><Skeleton className="h-24" /></div>}
            {id && detail.isError && !live && <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />}
            {messages.map((m, index) => m.role === "user"
              ? <UserMessage key={m.id} text={m.content} time={m.created_at} />
              : <AssistantMessage key={m.id} message={m} approvals={list} retrying={!!live} onRetry={() => retry(m, messages[index - 1]?.content ?? "")} />)}
            {live && (
              <>
                {!messages.some((m) => m.role === "user" && m.content === live.userText && m === messages[messages.length - 1]) && <UserMessage text={live.userText} />}
                <div className="flex gap-3" aria-live="polite">
                  <Avatar />
                  <div className="min-w-0 flex-1 space-y-2">
                    <Meta simulated={live.simulated} />
                    {!live.plan && <p className="flex items-center gap-2 text-[13px] text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Planning…</p>}
                    <ExecutionTrace plan={live.plan} steps={live.steps} tools={live.tools} running activeAgent={live.activeAgent} />
                    {live.text && <Markdown>{live.text}</Markdown>}
                    {live.ledger.some((e) => e.kind === "approval") && !live.text && <StatusBadge status="pending" label="Preparing approval" />}
                  </div>
                </div>
              </>
            )}
            <div ref={bottom} />
          </div>
        </div>
        <div className="border-t bg-background p-3">
          <form className="mx-auto flex max-w-3xl items-end gap-2 rounded-lg border border-input bg-background p-1.5 focus-within:ring-2 focus-within:ring-ring" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <textarea ref={textarea} aria-label="Message your AI employee" rows={1} value={input} maxLength={8000}
              onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(input); } }}
              placeholder="Ask your AI employee to do something…" className="max-h-40 min-h-8 flex-1 resize-none bg-transparent px-2 py-1.5 text-[14px] outline-none placeholder:text-muted-foreground focus-visible:ring-0" />
            <Button type="submit" size="icon" variant="primary" aria-label="Send" disabled={!input.trim() || !!live}>{live ? <Loader2 className="animate-spin" /> : <ArrowUp />}</Button>
          </form>
        </div>
      </section>
    </div>
  );
}
