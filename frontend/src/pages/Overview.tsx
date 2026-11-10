import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, Calendar, CheckSquare, Clock, Inbox, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { EmptyState, ErrorState, PageHeader, PriorityBadge, SimulatedBadge, StatCard, StatusBadge } from "@/components/common";
import { Badge, Button, Card, CardHeader, Skeleton } from "@/components/ui";
import { useAuth } from "@/contexts";
import { api } from "@/services/api";
import type { Dashboard } from "@/types";
import { cn, fmtDateTime, timeAgo, titleCase } from "@/utils";

const STATE_TEXT: Record<string, string> = { online: "Online", working: "Working", waiting_for_approval: "Waiting for approval", paused: "Paused", error: "Error" };
const STATE_DOT: Record<string, string> = { online: "bg-success", working: "bg-primary animate-pulse", waiting_for_approval: "bg-warning", paused: "bg-muted-foreground", error: "bg-danger" };

export function OverviewPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard"), refetchInterval: 30_000 });

  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (isPending) return <div className="space-y-4"><Skeleton className="h-8 w-48" /><div className="grid grid-cols-2 gap-3 md:grid-cols-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-20" />)}</div><Skeleton className="h-64" /></div>;

  const { stats, employee } = data;
  const ask = (prompt: string) => navigate("/ai", { state: { prompt } });
  return (
    <>
      <PageHeader title={`Good to see you, ${user?.full_name.split(" ")[0]}`} description={new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeZone: data.timezone }).format(new Date())} actions={<>{data.simulated && <SimulatedBadge label="Showing demo data" />}<Button variant="primary" asChild><Link to="/ai"><Bot />Delegate work</Link></Button></>} />

      <Card className="mb-4 flex flex-wrap items-center gap-3 px-4 py-3">
        <span className={cn("size-2.5 rounded-full", STATE_DOT[employee.state])} />
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold">AI employee: {STATE_TEXT[employee.state]}</p>
          <p className="text-xs text-muted-foreground">{employee.detail}</p>
        </div>
        {employee.pending_approvals > 0 && <Button size="sm" asChild><Link to="/approvals">Review approvals<ArrowRight /></Link></Button>}
      </Card>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Total tasks" value={stats.total_tasks} />
        <StatCard label="Completed" value={stats.completed_tasks} />
        <StatCard label="Pending" value={stats.pending_tasks} hint={stats.overdue_tasks ? `${stats.overdue_tasks} overdue` : undefined} />
        <StatCard label="Emails processed" value={stats.emails_processed} />
        <StatCard label="Upcoming deadlines" value={stats.upcoming_deadlines} hint="Next 7 days" />
        <StatCard label="Active automations" value={stats.active_automations} />
        <StatCard label="Upcoming meetings" value={data.upcoming_events.length} />
        <StatCard label="Awaiting approval" value={employee.pending_approvals} />
      </div>

      {data.recommendations.length > 0 && (
        <Card className="mt-4">
          <CardHeader title={<span className="flex items-center gap-1.5"><Sparkles className="size-3.5 text-primary" />Recommendations</span>} description="Based on the current state of your work" />
          <ul className="divide-y">
            {data.recommendations.map((r) => (
              <li key={r.title} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <div className="min-w-0 flex-1"><p className="text-[13px] font-medium">{r.title}</p><p className="text-xs text-muted-foreground">{r.reason}</p></div>
                {r.prompt && <Button size="sm" variant="ghost" onClick={() => ask(r.prompt!)}><Bot />Ask AI</Button>}
                <Button size="sm" asChild><Link to={r.link}>Open</Link></Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <h2 className="mb-2 mt-6 text-[13px] font-semibold">Today's work</h2>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Important emails" action={<Link to="/inbox?view=important" className="text-xs text-primary">View all</Link>} />
          {data.important_emails.length === 0 ? <EmptyState compact icon={Inbox} title="Nothing urgent" description="Important emails appear here after analysis." /> : (
            <ul className="divide-y">{data.important_emails.map((e) => (
              <li key={e.id}><Link to={`/inbox/${e.id}`} className="block px-4 py-2.5 hover:bg-accent/60">
                <p className="truncate text-[13px] font-medium">{e.subject}</p>
                <p className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">{e.sender}{e.category && <Badge>{e.category}</Badge>}</p>
                {e.deadline_at && <p className="mt-0.5 flex items-center gap-1 text-2xs text-warning"><Clock className="size-3" />Deadline {fmtDateTime(e.deadline_at, data.timezone)}</p>}
              </Link></li>
            ))}</ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Pending tasks" action={<Link to="/tasks?status=open" className="text-xs text-primary">View all</Link>} />
          {data.pending_tasks.length === 0 ? <EmptyState compact icon={CheckSquare} title="No open tasks" /> : (
            <ul className="divide-y">{data.pending_tasks.map((t) => (
              <li key={t.id} className="px-4 py-2.5">
                <p className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-[13px] font-medium">{t.title}</span><PriorityBadge priority={t.priority} /></p>
                <p className={cn("text-xs", t.overdue ? "text-danger" : "text-muted-foreground")}>{t.due_at ? `${t.overdue ? "Overdue · " : "Due "}${fmtDateTime(t.due_at, data.timezone)}` : "No deadline"}</p>
              </li>
            ))}</ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Upcoming meetings" action={<Link to="/calendar" className="text-xs text-primary">Calendar</Link>} />
          {data.upcoming_events.length === 0 ? <EmptyState compact icon={Calendar} title="No meetings ahead" /> : (
            <ul className="divide-y">{data.upcoming_events.map((e) => (
              <li key={e.id} className="px-4 py-2.5"><p className="truncate text-[13px] font-medium">{e.title}</p><p className="text-xs text-muted-foreground">{fmtDateTime(e.start_at, data.timezone)}</p></li>
            ))}</ul>
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Recent activity" action={<Link to="/activity" className="text-xs text-primary">View all</Link>} />
        {data.recent_activity.length === 0 ? <EmptyState compact icon={Bot} title="No activity yet" /> : (
          <ul className="divide-y">{data.recent_activity.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
              <span className="min-w-0 flex-1 truncate text-[13px]">{a.description}</span>
              {a.simulated && <SimulatedBadge />}
              {a.status === "failure" && <StatusBadge status="failure" />}
              <span className="text-xs text-muted-foreground">{titleCase(a.actor)} · {timeAgo(a.created_at)}</span>
            </li>
          ))}</ul>
        )}
      </Card>
    </>
  );
}
