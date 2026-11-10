import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState, ErrorState, PageHeader, SimulatedBadge, StatCard } from "@/components/common";
import { Card, CardHeader, Select, Skeleton } from "@/components/ui";
import { api } from "@/services/api";
import type { Analytics } from "@/types";
import { plural, titleCase } from "@/utils";

const axis = { stroke: "hsl(var(--muted-foreground))", fontSize: 11, tickLine: false, axisLine: false } as const;
const shortDate = (value: string) => new Date(`${value}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function ChartTooltip({ active, payload, label, labelFormat }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-pop">
      <p className="mb-0.5 font-medium">{labelFormat ? labelFormat(label) : label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} className="flex items-center gap-1.5 text-muted-foreground">
          <span className="inline-block size-2 rounded-sm" style={{ background: p.color }} />{p.name}<span className="ml-auto pl-3 font-medium tabular-nums text-foreground">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return <div className="flex gap-3 text-xs text-muted-foreground">{items.map(([label, color]) => <span key={label} className="flex items-center gap-1.5"><span className="inline-block size-2 rounded-sm" style={{ background: color }} />{label}</span>)}</div>;
}

const hasData = (rows: Record<string, any>[], keys: string[]) => rows.some((r) => keys.some((k) => r[k] > 0));
const NoData = () => <EmptyState compact icon={BarChart3} title="No data in this period" />;
const grid = <CartesianGrid vertical={false} stroke="var(--chart-grid)" />;
const cursor = { fill: "hsl(var(--accent))" };

export function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["analytics", days], queryFn: () => api.get<Analytics>(`/analytics?days=${days}`) });
  const actions = <>{data?.simulated && <SimulatedBadge label="Includes demo data" />}<Select aria-label="Time range" className="w-36" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></Select></>;

  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (isPending) return <><PageHeader title="Analytics" /><div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="h-20" />)}</div></>;

  const { totals, time_saved: saved } = data;
  const pct = (value: number | null) => (value == null ? "—" : `${value}%`);
  const tick = Math.max(1, Math.floor(data.task_trend.length / 7));
  return (
    <>
      <PageHeader title="Analytics" description="Calculated from your stored records." actions={actions} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Emails processed" value={totals.emails_processed} />
        <StatCard label="Tasks completed" value={totals.tasks_completed} />
        <StatCard label="Tasks pending" value={totals.tasks_pending} />
        <StatCard label="Automation success" value={pct(totals.automation_success_rate)} hint={plural(totals.automation_runs, "run")} />
        <StatCard label="Automation failure" value={pct(totals.automation_failure_rate)} />
        <StatCard label="Avg. run time" value={totals.avg_execution_ms == null ? "—" : `${(totals.avg_execution_ms / 1000).toFixed(1)}s`} />
        <StatCard label="Time saved (estimate)" value={`~${(saved.minutes_estimate / 60).toFixed(1)}h`} hint={saved.minutes_estimate ? "Estimated, not measured" : undefined} />
        <StatCard label="Most used agent" value={data.agents[0] ? titleCase(data.agents[0].agent) : "—"} hint={data.agents[0] ? plural(data.agents[0].runs, "run") : undefined} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Task completion trend" action={<Legend items={[["Completed", "var(--chart-1)"], ["Created", "var(--chart-2)"]]} />} />
          <div className="h-56 p-3">{hasData(data.task_trend, ["created", "completed"]) ? (
            <ResponsiveContainer><LineChart data={data.task_trend} margin={{ left: -24, right: 8, top: 4 }}>{grid}<XAxis dataKey="date" {...axis} tickFormatter={shortDate} interval={tick} /><YAxis {...axis} allowDecimals={false} /><Tooltip content={<ChartTooltip labelFormat={shortDate} />} /><Line dataKey="completed" name="Completed" stroke="var(--chart-1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} /><Line dataKey="created" name="Created" stroke="var(--chart-2)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} /></LineChart></ResponsiveContainer>
          ) : <NoData />}</div>
        </Card>
        <Card>
          <CardHeader title="Weekly productivity" description="Tasks completed per week" />
          <div className="h-56 p-3">{data.weekly_productivity.length ? (
            <ResponsiveContainer><BarChart data={data.weekly_productivity} margin={{ left: -24, right: 8, top: 4 }}>{grid}<XAxis dataKey="week" {...axis} tickFormatter={(w: string) => w.slice(5)} /><YAxis {...axis} allowDecimals={false} /><Tooltip cursor={cursor} content={<ChartTooltip />} /><Bar dataKey="completed" name="Completed" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} /></BarChart></ResponsiveContainer>
          ) : <NoData />}</div>
        </Card>
        <Card>
          <CardHeader title="Email categories" description="Analyzed emails by category" />
          <div className="h-56 p-3">{data.email_categories.length ? (
            <ResponsiveContainer><BarChart layout="vertical" data={data.email_categories} margin={{ left: 12, right: 12 }}><XAxis type="number" hide allowDecimals={false} /><YAxis type="category" dataKey="category" {...axis} width={84} /><Tooltip cursor={cursor} content={<ChartTooltip />} /><Bar dataKey="count" name="Emails" fill="var(--chart-1)" radius={[0, 4, 4, 0]} maxBarSize={16} /></BarChart></ResponsiveContainer>
          ) : <NoData />}</div>
        </Card>
        <Card>
          <CardHeader title="Automation runs" action={<Legend items={[["Succeeded", "var(--chart-1)"], ["Failed", "var(--chart-critical)"]]} />} />
          <div className="h-56 p-3">{hasData(data.automation_history, ["succeeded", "failed"]) ? (
            <ResponsiveContainer><BarChart data={data.automation_history} margin={{ left: -24, right: 8, top: 4 }}>{grid}<XAxis dataKey="date" {...axis} tickFormatter={shortDate} interval={tick} /><YAxis {...axis} allowDecimals={false} /><Tooltip cursor={cursor} content={<ChartTooltip labelFormat={shortDate} />} /><Bar dataKey="succeeded" name="Succeeded" stackId="a" fill="var(--chart-1)" stroke="hsl(var(--background))" strokeWidth={1} maxBarSize={14} /><Bar dataKey="failed" name="Failed" stackId="a" fill="var(--chart-critical)" stroke="hsl(var(--background))" strokeWidth={1} radius={[4, 4, 0, 0]} maxBarSize={14} /></BarChart></ResponsiveContainer>
          ) : <NoData />}</div>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Agent usage" />
          {data.agents.length ? (
            <table className="w-full text-[13px]"><thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="px-4 py-2 font-medium">Agent</th><th className="px-4 py-2 text-right font-medium">Runs</th><th className="px-4 py-2 text-right font-medium">Avg. time</th></tr></thead>
              <tbody className="divide-y">{data.agents.map((a) => <tr key={a.agent}><td className="px-4 py-2">{titleCase(a.agent)}</td><td className="px-4 py-2 text-right tabular-nums">{a.runs}</td><td className="px-4 py-2 text-right tabular-nums">{(a.avg_ms / 1000).toFixed(2)}s</td></tr>)}</tbody>
            </table>
          ) : <NoData />}
        </Card>
        <Card>
          <CardHeader title="How the time-saved estimate works" />
          <div className="space-y-2 p-4 text-[13px]">
            <p className="text-muted-foreground">{saved.explanation}</p>
            <table className="w-full text-xs"><thead><tr className="border-b text-left text-muted-foreground"><th className="py-1 font-medium">Action</th><th className="py-1 text-right font-medium">Count</th><th className="py-1 text-right font-medium">Assumed min each</th></tr></thead>
              <tbody>{Object.entries(saved.counts).map(([key, count]) => <tr key={key}><td className="py-0.5">{titleCase(key)}</td><td className="py-0.5 text-right tabular-nums">{count}</td><td className="py-0.5 text-right tabular-nums">{saved.assumed_minutes_per_action[key]}</td></tr>)}</tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}
