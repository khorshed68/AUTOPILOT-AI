import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarPlus, Check, CheckSquare, FileUp, Mail, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Card, Field, Input, Select, Textarea } from "@/components/ui";
import { ApiError, api } from "@/services/api";
import type { Approval } from "@/types";
import { errorMessage, fmtDateTime, titleCase } from "@/utils";

const ACTIONS: Record<string, { label: string; icon: typeof Mail }> = {
  send_email: { label: "Send email", icon: Mail },
  create_calendar_event: { label: "Create calendar event", icon: CalendarPlus },
  update_calendar_event: { label: "Update calendar event", icon: CalendarPlus },
  delete_calendar_event: { label: "Delete calendar event", icon: Trash2 },
  share_document: { label: "Share document", icon: FileUp },
  create_task: { label: "Create task", icon: CheckSquare },
};

const list = (value: string) => value.split(",").map((s) => s.trim()).filter(Boolean);
/** Keep the wall-clock editable while preserving the event's own UTC offset. */
const withOffset = (local: string, original: string) => `${local.length === 16 ? `${local}:00` : local}${original.slice(19)}`;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[84px_1fr] gap-2 py-0.5 text-[13px]">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 whitespace-pre-wrap break-words">{children}</dd>
    </div>
  );
}

function Details({ approval }: { approval: Approval }) {
  const p = approval.payload;
  switch (approval.action_type) {
    case "send_email":
      return (<dl><Row label="To">{p.to.join(", ")}</Row>{p.cc?.length > 0 && <Row label="Cc">{p.cc.join(", ")}</Row>}<Row label="Subject">{p.subject}</Row><Row label="Message">{p.body}</Row></dl>);
    case "create_calendar_event":
    case "update_calendar_event":
      return (
        <dl>
          <Row label="Description">{p.title}</Row>
          <Row label="Date">{fmtDateTime(p.start_at, p.timezone).replace(/,? \d{2}:\d{2}.*$/, "")}</Row>
          <Row label="Time">{p.start_at.slice(11, 16)} – {p.end_at.slice(11, 16)} ({p.timezone})</Row>
          {p.attendees?.length > 0 && <Row label="Attendees">{p.attendees.join(", ")}</Row>}
          {p.location && <Row label="Location">{p.location}</Row>}
        </dl>
      );
    case "delete_calendar_event":
      return <dl><Row label="Event">{p.title}</Row></dl>;
    case "share_document":
      return <dl><Row label="Document">{p.title}</Row><Row label="With">{p.email}</Row><Row label="Access">{titleCase(p.role)}</Row></dl>;
    case "create_task":
      return <dl><Row label="Task">{p.title}</Row><Row label="Priority">{titleCase(p.priority)}</Row><Row label="Due">{p.due_at ? fmtDateTime(p.due_at) : "No deadline"}</Row></dl>;
    default:
      return <pre className="overflow-x-auto text-xs">{JSON.stringify(p, null, 2)}</pre>;
  }
}

function EditForm({ approval, onSave, onCancel, saving }: { approval: Approval; onSave: (payload: Record<string, any>) => void; onCancel: () => void; saving: boolean }) {
  const [draft, setDraft] = useState<Record<string, any>>(() => ({ ...approval.payload }));
  const [recipients, setRecipients] = useState((approval.payload.to ?? approval.payload.attendees ?? []).join(", "));
  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d, [key]: value }));
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (approval.action_type === "send_email") onSave({ ...draft, to: list(recipients) });
    else if (approval.action_type.endsWith("calendar_event") && approval.action_type !== "delete_calendar_event") onSave({ ...draft, attendees: list(recipients) });
    else onSave(draft);
  };
  const isEvent = approval.action_type === "create_calendar_event" || approval.action_type === "update_calendar_event";
  return (
    <form onSubmit={submit} className="space-y-3">
      {approval.action_type === "send_email" && (
        <>
          <Field label="To" htmlFor="ap-to"><Input id="ap-to" value={recipients} onChange={(e) => setRecipients(e.target.value)} /></Field>
          <Field label="Subject" htmlFor="ap-subject"><Input id="ap-subject" value={draft.subject} onChange={(e) => set("subject", e.target.value)} /></Field>
          <Field label="Message" htmlFor="ap-body"><Textarea id="ap-body" rows={8} value={draft.body} onChange={(e) => set("body", e.target.value)} /></Field>
        </>
      )}
      {isEvent && (
        <>
          <Field label="Title" htmlFor="ap-title"><Input id="ap-title" value={draft.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={`Start (${draft.timezone})`} htmlFor="ap-start"><Input id="ap-start" type="datetime-local" value={draft.start_at.slice(0, 16)} onChange={(e) => set("start_at", withOffset(e.target.value, approval.payload.start_at))} /></Field>
            <Field label={`End (${draft.timezone})`} htmlFor="ap-end"><Input id="ap-end" type="datetime-local" value={draft.end_at.slice(0, 16)} onChange={(e) => set("end_at", withOffset(e.target.value, approval.payload.end_at))} /></Field>
          </div>
          <Field label="Attendees" hint="Email addresses, separated by commas" htmlFor="ap-att"><Input id="ap-att" value={recipients} onChange={(e) => setRecipients(e.target.value)} /></Field>
        </>
      )}
      {approval.action_type === "create_task" && (
        <>
          <Field label="Title" htmlFor="ap-title"><Input id="ap-title" value={draft.title} onChange={(e) => set("title", e.target.value)} /></Field>
          <Field label="Priority" htmlFor="ap-pri"><Select id="ap-pri" value={draft.priority} onChange={(e) => set("priority", e.target.value)}>{["critical", "high", "medium", "low"].map((p) => <option key={p} value={p}>{titleCase(p)}</option>)}</Select></Field>
        </>
      )}
      {approval.action_type === "share_document" && (
        <>
          <Field label="Share with" htmlFor="ap-email"><Input id="ap-email" type="email" value={draft.email} onChange={(e) => set("email", e.target.value)} /></Field>
          <Field label="Access" htmlFor="ap-role"><Select id="ap-role" value={draft.role} onChange={(e) => set("role", e.target.value)}>{["reader", "commenter", "writer"].map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}</Select></Field>
        </>
      )}
      <p className="text-xs text-muted-foreground">Saving changes creates a new version. You'll still need to approve it.</p>
      <div className="flex justify-end gap-2">
        <Button size="sm" onClick={onCancel}>Cancel</Button>
        <Button size="sm" variant="primary" type="submit" loading={saving}>Save changes</Button>
      </div>
    </form>
  );
}

const EDITABLE = new Set(["send_email", "create_calendar_event", "update_calendar_event", "create_task", "share_document"]);

export function ApprovalCard({ approval, onChanged }: { approval: Approval; onChanged?: () => void }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const action = ACTIONS[approval.action_type] ?? { label: titleCase(approval.action_type), icon: Check };
  const Icon = action.icon;

  const settle = () => {
    for (const key of ["approvals", "employee", "tasks", "calendar", "emails", "notifications", "activity", "dashboard"]) void queryClient.invalidateQueries({ queryKey: [key] });
    onChanged?.();
  };
  const onError = (error: unknown) => {
    toast.error(errorMessage(error));
    if (error instanceof ApiError && error.status === 409) settle();
  };
  const approve = useMutation({
    mutationFn: () => api.post<Approval>(`/approvals/${approval.id}/approve`, { payload_hash: approval.payload_hash }),
    onSuccess: (result) => {
      if (result.status === "executed") toast.success(result.simulated ? "Simulated: action recorded. No real service was contacted." : "Done. The service confirmed it.");
      else toast.error(result.error ?? "The action did not complete.");
      settle();
    },
    onError,
  });
  const reject = useMutation({ mutationFn: () => api.post<Approval>(`/approvals/${approval.id}/reject`, {}), onSuccess: settle, onError });
  const edit = useMutation({
    mutationFn: (payload: Record<string, any>) => api.put<Approval>(`/approvals/${approval.id}`, { expected_hash: approval.payload_hash, payload }),
    onSuccess: () => { setEditing(false); toast.success("Saved. Review and approve when ready."); settle(); },
    onError,
  });

  const pending = approval.status === "pending";
  return (
    <Card className="overflow-hidden" data-testid="approval-card">
      <div className="flex flex-wrap items-center gap-2 border-b bg-surface px-3 py-2">
        <Icon className="size-3.5 text-muted-foreground" />
        <span className="text-[13px] font-semibold">{action.label}</span>
        <div className="ml-auto flex items-center gap-1.5">
          {approval.simulated && <SimulatedBadge />}
          <StatusBadge status={approval.status} label={pending ? "Waiting for approval" : undefined} />
        </div>
      </div>
      <div className="space-y-2 p-3">
        {editing ? <EditForm approval={approval} saving={edit.isPending} onCancel={() => setEditing(false)} onSave={(p) => edit.mutate(p)} /> : <Details approval={approval} />}
        {approval.derived_from_untrusted && pending && !editing && (
          <p className="flex gap-1.5 rounded bg-warning/10 px-2 py-1.5 text-xs text-warning">
            <AlertTriangle className="mt-px size-3.5 shrink-0" />
            Prepared using content from an email or document. Check the details before approving.
          </p>
        )}
        {approval.status === "executed" && approval.result && (
          <p className="text-xs text-success">{approval.simulated ? "Simulated, not sent to a real service." : "Confirmed by the service."}{approval.result.external_id ? ` Reference: ${approval.result.external_id}` : ""}</p>
        )}
        {approval.error && approval.status !== "rejected" && <p role="alert" className="text-xs text-danger">{approval.error}</p>}
        {approval.status === "outcome_unknown" && <p className="text-xs text-muted-foreground">Check the external service before trying this again, so it doesn't happen twice.</p>}
        {pending && !editing && (
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            {EDITABLE.has(approval.action_type) && <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil />Edit</Button>}
            <Button size="sm" onClick={() => reject.mutate()} loading={reject.isPending} disabled={approve.isPending}><X />Reject</Button>
            <Button size="sm" variant="primary" onClick={() => approve.mutate()} loading={approve.isPending} disabled={reject.isPending}><Check />Approve</Button>
          </div>
        )}
      </div>
    </Card>
  );
}
