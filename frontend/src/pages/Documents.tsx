import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileBarChart, FileText, GitCompare, Pencil, Share2, ShieldAlert, Sparkles, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorState, Markdown, PageHeader, Pagination, RowsSkeleton, SimulatedBadge, StatusBadge } from "@/components/common";
import { Badge, Button, Card, CardHeader, ConfirmDialog, Dialog, DialogContent, Field, Input, Select, Tabs, TabsList, TabsTrigger, Textarea } from "@/components/ui";
import { api } from "@/services/api";
import type { Approval, DocumentItem, Page } from "@/types";
import { errorMessage, fmtBytes, fmtDate, timeAgo } from "@/utils";

const ALLOWED = ".pdf,.docx,.txt,.csv";

function ShareDialog({ doc, open, onOpenChange }: { doc: DocumentItem; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("reader");
  const [approval, setApproval] = useState<Approval | null>(null);
  const propose = useMutation({ mutationFn: () => api.post<Approval>(`/documents/${doc.id}/share-proposal`, { email, role }), onSuccess: setApproval, onError: (e) => toast.error(errorMessage(e)) });
  const close = (o: boolean) => { if (!o) setApproval(null); onOpenChange(o); };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent title={`Share ${doc.title}`} description="Sharing needs your explicit approval.">
        {approval ? <ApprovalCard approval={approval} onChanged={() => close(false)} /> : (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); propose.mutate(); }}>
            <Field label="Email" htmlFor="share-email"><Input id="share-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            <Field label="Access" htmlFor="share-role"><Select id="share-role" value={role} onChange={(e) => setRole(e.target.value)}><option value="reader">Reader</option><option value="commenter">Commenter</option><option value="writer">Writer</option></Select></Field>
            <div className="flex justify-end gap-2"><Button onClick={() => close(false)}>Cancel</Button><Button variant="primary" type="submit" loading={propose.isPending}>Review</Button></div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function DocumentDetail({ id }: { id: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: doc, isPending, isError, error, refetch } = useQuery({ queryKey: ["documents", "detail", id], queryFn: () => api.get<DocumentItem>(`/documents/${id}`) });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [sharing, setSharing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents"] });
  const analyze = useMutation({ mutationFn: () => api.post<DocumentItem>(`/documents/${id}/analyze`), onSuccess: () => void refresh(), onError: (e) => toast.error(errorMessage(e)) });
  const save = useMutation({ mutationFn: () => api.put(`/documents/${id}`, { content: draft }), onSuccess: () => { setEditing(false); toast.success("Report saved"); void refresh(); }, onError: (e) => toast.error(errorMessage(e)) });
  const remove = useMutation({ mutationFn: () => api.del(`/documents/${id}`), onSuccess: () => { toast.success("Document deleted"); void refresh(); navigate("/documents"); }, onError: (e) => toast.error(errorMessage(e)) });

  if (isPending) return <RowsSkeleton rows={8} />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  const report = doc.kind === "report";
  return (
    <>
      <Button variant="ghost" size="sm" asChild className="mb-2"><Link to="/documents"><ArrowLeft />Documents</Link></Button>
      <PageHeader title={doc.title} description={`${doc.filename} · ${fmtBytes(doc.size_bytes)} · ${fmtDate(doc.created_at)}`} actions={
        <>
          {!report && doc.status === "ready" && <Button onClick={() => analyze.mutate()} loading={analyze.isPending}><Sparkles />{doc.analysis ? "Re-analyze" : "Analyze"}</Button>}
          {report && !editing && <Button onClick={() => { setDraft(doc.content ?? ""); setEditing(true); }}><Pencil />Edit</Button>}
          {report && <Button asChild><a href={`/api/documents/${id}/export?format=pdf`}><Download />PDF</a></Button>}
          {report && <Button asChild><a href={`/api/documents/${id}/export?format=docx`}><Download />DOCX</a></Button>}
          <Button onClick={() => setSharing(true)}><Share2 />Share</Button>
          <Button variant="danger" onClick={() => setDeleting(true)}><Trash2 />Delete</Button>
        </>
      } />
      {doc.injection_flags.length > 0 && <p className="mb-3 flex gap-1.5 rounded-md bg-danger/10 px-2.5 py-2 text-xs text-danger"><ShieldAlert className="mt-px size-3.5 shrink-0" />This document contains text that tries to instruct an AI. It's treated as data only.</p>}
      {doc.status === "failed" && <p role="alert" className="mb-3 rounded-md bg-danger/10 px-2.5 py-2 text-xs text-danger">{doc.error}</p>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title={report ? "Report" : "Extracted text"} action={doc.is_demo ? <SimulatedBadge label="Demo" /> : undefined} />
          <div className="max-h-[70vh] overflow-y-auto p-4">
            {editing ? (
              <div className="space-y-2">
                <Textarea aria-label="Report content" rows={22} className="font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} />
                <div className="flex justify-end gap-2"><Button size="sm" onClick={() => setEditing(false)}>Cancel</Button><Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button></div>
              </div>
            ) : report ? <Markdown>{doc.content ?? ""}</Markdown> : <pre className="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed">{doc.content}</pre>}
          </div>
        </Card>
        <div className="space-y-4">
          {report && (
            <Card>
              <CardHeader title="Sources" description="Documents this report draws on" />
              {doc.sources.length ? <ul className="divide-y">{doc.sources.map((s, i) => <li key={s.id} className="px-4 py-2 text-[13px]"><Link className="hover:underline" to={`/documents/${s.id}`}><span className="text-muted-foreground">[S{i + 1}]</span> {s.title}</Link></li>)}</ul> : <p className="px-4 py-3 text-xs text-muted-foreground">Computed from your stored work data.</p>}
            </Card>
          )}
          {doc.analysis && (
            <Card>
              <CardHeader title="Analysis" action={doc.analysis.simulated_ai ? <SimulatedBadge label="Demo AI" /> : undefined} />
              <div className="space-y-3 p-4 text-[13px]">
                <p>{doc.analysis.summary}</p>
                {doc.analysis.key_points.length > 0 && <div><p className="mb-1 text-xs font-medium">Key points</p><ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground">{doc.analysis.key_points.map((k) => <li key={k}>{k}</li>)}</ul></div>}
                {doc.analysis.dates.length > 0 && <div><p className="mb-1 text-xs font-medium">Dates mentioned</p><ul className="space-y-0.5 text-xs text-muted-foreground">{doc.analysis.dates.map((d) => <li key={d.date + d.text}><span className="font-medium text-foreground">{d.date}</span> · {d.text}</li>)}</ul></div>}
              </div>
            </Card>
          )}
        </div>
      </div>
      <ShareDialog doc={doc} open={sharing} onOpenChange={setSharing} />
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title="Delete this document?" description="It will be permanently removed from AutoPilot." confirmLabel="Delete" loading={remove.isPending} onConfirm={() => remove.mutate()} />
    </>
  );
}

export function DocumentsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [reportOpen, setReportOpen] = useState(false);
  const [topic, setTopic] = useState("");
  const [comparison, setComparison] = useState<{ summary: string; similarities: string[]; differences: string[]; simulated_ai: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);

  const query = new URLSearchParams({ page: String(page), page_size: "20", ...(kind !== "all" && { kind }) });
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["documents", query.toString()], queryFn: () => api.get<Page<DocumentItem>>(`/documents?${query}`), enabled: !id });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents"] });
  const upload = useMutation({
    mutationFn: async (files: File[]) => { for (const file of files) { const form = new FormData(); form.append("file", file); await api.upload("/documents/upload", form); } },
    onSuccess: () => { toast.success("Uploaded"); void refresh(); }, onError: (e) => { toast.error(errorMessage(e)); void refresh(); },
  });
  const report = useMutation({ mutationFn: () => api.post<DocumentItem>("/documents/reports", { document_ids: selected, topic }), onSuccess: (r) => { setReportOpen(false); setSelected([]); void refresh(); navigate(`/documents/${r.id}`); }, onError: (e) => toast.error(errorMessage(e)) });
  const compare = useMutation({ mutationFn: () => api.post<NonNullable<typeof comparison>>("/documents/compare", { document_ids: selected }), onSuccess: setComparison, onError: (e) => toast.error(errorMessage(e)) });

  if (id) return <DocumentDetail id={id} />;
  const toggle = (docId: string) => setSelected((s) => (s.includes(docId) ? s.filter((x) => x !== docId) : [...s, docId]));
  return (
    <>
      <PageHeader title="Documents" description="Upload files, then analyze, compare, or turn them into sourced reports." actions={
        <>
          {selected.length >= 2 && <Button onClick={() => compare.mutate()} loading={compare.isPending}><GitCompare />Compare ({selected.length})</Button>}
          {selected.length >= 1 && <Button onClick={() => setReportOpen(true)}><FileBarChart />Generate report ({selected.length})</Button>}
          <Button variant="primary" onClick={() => input.current?.click()} loading={upload.isPending}><Upload />Upload</Button>
          <input ref={input} type="file" multiple accept={ALLOWED} className="hidden" aria-label="Upload documents" onChange={(e) => { if (e.target.files?.length) upload.mutate([...e.target.files]); e.target.value = ""; }} />
        </>
      } />
      <Card onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) upload.mutate([...e.dataTransfer.files]); }} className={dragging ? "ring-2 ring-ring" : undefined}>
        <Tabs value={kind} onValueChange={(v) => { setKind(v); setPage(1); }}>
          <TabsList className="px-2"><TabsTrigger value="all">All</TabsTrigger><TabsTrigger value="upload">Uploads</TabsTrigger><TabsTrigger value="report">Reports</TabsTrigger><TabsTrigger value="drive">Drive</TabsTrigger></TabsList>
        </Tabs>
        {isPending ? <RowsSkeleton rows={6} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : data.items.length === 0 ? <EmptyState icon={FileText} title="No documents yet" description="Drop a PDF, DOCX, TXT or CSV file here, up to 15 MB." action={<Button onClick={() => input.current?.click()}><Upload />Choose files</Button>} /> : (
          <ul className="divide-y">
            {data.items.map((d) => (
              <li key={d.id} className="flex items-center gap-3 px-3 py-2.5">
                <input type="checkbox" aria-label={`Select ${d.title}`} disabled={d.kind === "report" || d.status !== "ready"} checked={selected.includes(d.id)} onChange={() => toggle(d.id)} className="accent-[hsl(var(--primary))]" />
                {d.kind === "report" ? <FileBarChart className="size-4 shrink-0 text-primary" /> : <FileText className="size-4 shrink-0 text-muted-foreground" />}
                <Link to={`/documents/${d.id}`} className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-medium hover:underline">{d.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{d.filename} · {fmtBytes(d.size_bytes)} · {timeAgo(d.created_at)}</span>
                </Link>
                {d.status === "failed" && <StatusBadge status="failed" label="Unreadable" />}
                {d.analysis && <Badge tone="primary">Analyzed</Badge>}
                {d.kind === "report" && <Badge>Report</Badge>}
                {d.is_demo && <SimulatedBadge label="Demo" />}
              </li>
            ))}
          </ul>
        )}
        {data && <Pagination page={data.page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
      </Card>
      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent title="Generate a report" description={`Uses only the ${selected.length} selected document(s). Facts are cited; anything else is labelled a suggestion.`}>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (topic.trim()) report.mutate(); }}>
            <Field label="What should it cover?" htmlFor="r-topic"><Input id="r-topic" autoFocus value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Project Atlas milestones and budget" /></Field>
            <div className="flex justify-end gap-2"><Button onClick={() => setReportOpen(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={!topic.trim()} loading={report.isPending}>Generate</Button></div>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={!!comparison} onOpenChange={(o) => !o && setComparison(null)}>
        {comparison && (
          <DialogContent title="Comparison" description={comparison.summary}>
            <div className="space-y-3 text-[13px]">
              {comparison.simulated_ai && <SimulatedBadge label="Demo AI" />}
              <div><p className="mb-1 text-xs font-medium">Differences</p><ul className="list-disc space-y-1 pl-4">{comparison.differences.map((d) => <li key={d}>{d}</li>)}</ul></div>
              <div><p className="mb-1 text-xs font-medium">In common</p><p className="text-muted-foreground">{comparison.similarities.join(", ") || "None"}</p></div>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}
