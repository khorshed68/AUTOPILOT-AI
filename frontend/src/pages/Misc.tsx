import { useQuery } from "@tanstack/react-query";
import { Activity as ActivityIcon, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorState, PageHeader, Pagination, RowsSkeleton, SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Card, Input, Select, Tabs, TabsList, TabsTrigger } from "@/components/ui";
import { api } from "@/services/api";
import type { ActivityItem, Approval, Page } from "@/types";
import { fmtDateTime, titleCase } from "@/utils";

export function ApprovalsPage() {
  const [tab, setTab] = useState("pending");
  const [page, setPage] = useState(1);
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["approvals", "page", tab, page], queryFn: () => api.get<Page<Approval>>(`/approvals?page=${page}&page_size=20${tab === "pending" ? "&status=pending" : ""}`) });
  const items = tab === "pending" ? data?.items : data?.items.filter((a) => a.status !== "pending");
  return (
    <>
      <PageHeader title="Approvals" description="Nothing sensitive runs until you approve this exact version of it." />
      <Tabs value={tab} onValueChange={(v) => { setTab(v); setPage(1); }}><TabsList className="mb-4"><TabsTrigger value="pending">Waiting</TabsTrigger><TabsTrigger value="history">History</TabsTrigger></TabsList></Tabs>
      {isPending ? <RowsSkeleton /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : !items?.length ? <Card><EmptyState icon={ShieldCheck} title={tab === "pending" ? "Nothing waiting for you" : "No decisions yet"} description="Proposed actions will appear here." /></Card> : (
        <div className="mx-auto max-w-2xl space-y-3">
          {items.map((a) => (
            <div key={a.id}>
              <p className="mb-1 text-xs text-muted-foreground">{a.title} · proposed by {titleCase(a.requested_by)} · {fmtDateTime(a.created_at)}</p>
              <ApprovalCard approval={a} />
            </div>
          ))}
          <Pagination page={data!.page} pageSize={data!.page_size} total={data!.total} onChange={setPage} />
        </div>
      )}
    </>
  );
}

export function ActivityPage() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => { const t = setTimeout(() => { setTerm(q.trim()); setPage(1); }, 250); return () => clearTimeout(t); }, [q]);
  const query = new URLSearchParams({ page: String(page), page_size: "30", ...(status && { status }), ...(term && { q: term }) });
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["activity", query.toString()], queryFn: () => api.get<Page<ActivityItem>>(`/activity?${query}`) });
  return (
    <>
      <PageHeader title="Activity History" description="An audit trail of what you, your agents and the system have done." />
      <Card>
        <div className="flex flex-wrap gap-2 border-b p-2">
          <Input aria-label="Search activity" className="w-full sm:w-64" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select aria-label="Status" className="w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All outcomes</option><option value="success">Success</option><option value="failure">Failed</option><option value="pending">Pending</option><option value="info">Info</option></Select>
        </div>
        {isPending ? <RowsSkeleton rows={10} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.items.length === 0 ? <EmptyState icon={ActivityIcon} title="No activity found" /> : (
          <ul className="divide-y">
            {data.items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                <span className="min-w-0 flex-1 basis-64 text-[13px]">{a.description}</span>
                {a.simulated && <SimulatedBadge />}
                <StatusBadge status={a.status} />
                <span className="w-20 text-xs text-muted-foreground">{titleCase(a.actor)}</span>
                <time className="text-xs text-muted-foreground" dateTime={a.created_at}>{fmtDateTime(a.created_at)}</time>
              </li>
            ))}
          </ul>
        )}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
      </Card>
    </>
  );
}

export function NotFoundPage() {
  return <div className="py-24 text-center"><p className="text-sm font-medium">Page not found</p><Button className="mt-3" asChild><Link to="/">Back to overview</Link></Button></div>;
}
