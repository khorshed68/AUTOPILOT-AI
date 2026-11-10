import { AlertCircle, ChevronLeft, ChevronRight, FlaskConical, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import type { Priority } from "@/types";
import { cn, errorMessage, titleCase } from "@/utils";

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action, compact }: { icon: LucideIcon; title: string; description?: string; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 text-center", compact ? "py-8" : "py-16")}>
      <div className="mb-3 flex size-9 items-center justify-center rounded-lg border bg-surface text-muted-foreground"><Icon className="size-4" /></div>
      <p className="text-[13px] font-medium">{title}</p>
      {description && <p className="mt-1 max-w-sm text-xs text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <AlertCircle className="mb-2 size-5 text-danger" />
      <p className="text-[13px] font-medium">Couldn't load this</p>
      <p className="mt-1 max-w-sm text-xs text-muted-foreground">{errorMessage(error)}</p>
      {onRetry && <Button className="mt-3" size="sm" onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export const RowsSkeleton = ({ rows = 5 }: { rows?: number }) => (
  <div className="space-y-2 p-4" aria-label="Loading">{Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-9" />)}</div>
);

/** Marks anything that did not happen against a real external service. */
export const SimulatedBadge = ({ label = "Simulated" }: { label?: string }) => (
  <Badge tone="warning" className="border border-dashed border-warning/40 bg-transparent" title="Simulated demo data. Nothing here touched a real external service."><FlaskConical />{label}</Badge>
);

const PRIORITY_TONES = { critical: "danger", high: "warning", medium: "primary", low: "neutral" } as const;
export const PriorityBadge = ({ priority }: { priority: Priority }) => <Badge tone={PRIORITY_TONES[priority]}>{titleCase(priority)}</Badge>;

const STATUS_TONES: Record<string, "neutral" | "primary" | "success" | "warning" | "danger"> = {
  pending: "warning", executing: "primary", executed: "success", failed: "danger", rejected: "neutral", expired: "neutral", outcome_unknown: "danger",
  queued: "neutral", running: "primary", succeeded: "success", skipped: "neutral", completed: "success", in_progress: "primary", waiting: "warning", cancelled: "neutral", needs_input: "warning",
  success: "success", failure: "danger", info: "neutral",
};
const STATUS_LABELS: Record<string, string> = { pending: "Pending", executed: "Completed", outcome_unknown: "Outcome unknown", needs_input: "Needs input", failure: "Failed", success: "Success" };
export const StatusBadge = ({ status, label }: { status: string; label?: string }) => <Badge tone={STATUS_TONES[status] ?? "neutral"}>{label ?? STATUS_LABELS[status] ?? titleCase(status)}</Badge>;

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return null;
  return (
    <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-muted-foreground">
      <span>{total} total</span>
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft /></Button>
        <span>Page {page} of {pages}</span>
        <Button size="icon" variant="ghost" aria-label="Next page" disabled={page >= pages} onClick={() => onChange(page + 1)}><ChevronRight /></Button>
      </div>
    </div>
  );
}

/** AI text can be influenced by emails and documents. A remote image would leak data the moment it renders. */
const MARKDOWN_COMPONENTS: Components = {
  img: ({ alt }) => <span className="text-muted-foreground">[image removed{alt ? `: ${alt}` : ""}]</span>,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>,
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  return <div className={cn("prose-chat", className)}><ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>{children}</ReactMarkdown></div>;
}

export function StatCard({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <Card className="px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="mt-0.5 text-2xs text-muted-foreground">{hint}</p>}
    </Card>
  );
}
