import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ApprovalCard } from "@/components/ApprovalCard";
import { EmptyState, ErrorState, PageHeader, RowsSkeleton, SimulatedBadge, StatusBadge } from "@/components/common";
import { Button, Card, Dialog, DialogContent, Field, Input, Textarea } from "@/components/ui";
import { useAuth } from "@/contexts";
import { api } from "@/services/api";
import type { Approval, CalendarEvent } from "@/types";
import { cn, errorMessage, fmtDateTime, fmtTime, toLocalInput, zonedDate } from "@/utils";

const START_HOUR = 7;
const END_HOUR = 21;
const HOUR_PX = 44;

function startOfWeek(date: Date): Date {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}
const addDays = (d: Date, days: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
const isoDate = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function NewEventDialog({ open, onOpenChange, tz }: { open: boolean; onOpenChange: (o: boolean) => void; tz: string }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ title: "", date: isoDate(addDays(zonedDate(new Date()), 1)), start: "10:00", duration: 60, attendees: "", description: "" });
  const [result, setResult] = useState<{ approval: Approval; conflicts: { title: string }[] } | null>(null);
  const [error, setError] = useState("");
  const set = (key: string, value: string | number) => setForm((f) => ({ ...f, [key]: value }));
  const slots = useQuery({ queryKey: ["calendar", "slots", form.date, form.duration], queryFn: () => api.get<{ slots: { label: string; start_local: string }[]; warning?: string }>(`/calendar/availability?date=${form.date}&duration_minutes=${form.duration}`), enabled: open && !!form.date && form.duration >= 5 });
  const close = (o: boolean) => { if (!o) { setResult(null); setError(""); } onOpenChange(o); };
  const propose = useMutation({
    mutationFn: () => {
      const [h, m] = form.start.split(":").map(Number);
      const end = new Date(2000, 0, 1, h, m + form.duration);
      const endDate = end.getDate() === 1 ? form.date : isoDate(addDays(new Date(`${form.date}T00:00:00`), 1));
      return api.post<{ approval: Approval; conflicts: { title: string }[] }>("/calendar/events/proposals", {
        title: form.title, start_at: `${form.date}T${form.start}:00`, end_at: `${endDate}T${String(end.getHours()).padStart(2, "0")}:${String(end.getMinutes()).padStart(2, "0")}:00`,
        description: form.description, attendees: form.attendees.split(",").map((s) => s.trim()).filter(Boolean),
      });
    },
    onSuccess: (r) => { setResult(r); void queryClient.invalidateQueries({ queryKey: ["calendar"] }); void queryClient.invalidateQueries({ queryKey: ["approvals"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const submit = (event: React.FormEvent) => { event.preventDefault(); if (!form.title.trim()) { setError("Give the event a title."); return; } setError(""); propose.mutate(); };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent title={result ? "Review your event" : "New event"} description={result ? "This event isn't on your calendar until you approve it." : `Times are in ${tz}.`}>
        {result ? (
          <div className="space-y-3">
            {result.conflicts.length > 0 && <p role="alert" className="rounded bg-warning/10 px-2 py-1.5 text-xs text-warning">Conflicts with: {result.conflicts.map((c) => c.title).join(", ")}</p>}
            <ApprovalCard approval={result.approval} onChanged={() => close(false)} />
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-3" noValidate>
            <Field label="Title" error={error} htmlFor="e-title"><Input id="e-title" autoFocus value={form.title} onChange={(e) => set("title", e.target.value)} /></Field>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Date" htmlFor="e-date"><Input id="e-date" type="date" value={form.date} onChange={(e) => set("date", e.target.value)} /></Field>
              <Field label="Start" htmlFor="e-start"><Input id="e-start" type="time" value={form.start} onChange={(e) => set("start", e.target.value)} /></Field>
              <Field label="Minutes" htmlFor="e-dur"><Input id="e-dur" type="number" min={5} max={480} step={5} value={form.duration} onChange={(e) => set("duration", Number(e.target.value))} /></Field>
            </div>
            {slots.data && (
              <div>
                <p className="mb-1 text-xs text-muted-foreground">{slots.data.slots.length ? "Open slots" : "No open slots during working hours"}</p>
                <div className="flex flex-wrap gap-1">{slots.data.slots.map((s) => <button type="button" key={s.label} onClick={() => set("start", s.start_local)} className={cn("rounded border px-1.5 py-0.5 text-xs hover:bg-accent", form.start === s.start_local && "border-primary text-primary")}>{s.label}</button>)}</div>
                {slots.data.warning && <p className="mt-1 text-xs text-warning">{slots.data.warning}</p>}
              </div>
            )}
            <Field label="Attendees" hint="Email addresses, separated by commas" htmlFor="e-att"><Input id="e-att" value={form.attendees} onChange={(e) => set("attendees", e.target.value)} /></Field>
            <Field label="Description" htmlFor="e-desc"><Textarea id="e-desc" value={form.description} onChange={(e) => set("description", e.target.value)} /></Field>
            <div className="flex justify-end gap-2"><Button onClick={() => close(false)}>Cancel</Button><Button variant="primary" type="submit" loading={propose.isPending}>Propose event</Button></div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function EventDialog({ event, onClose }: { event: CalendarEvent | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [proposal, setProposal] = useState<Approval | null>(null);
  const [form, setForm] = useState<{ title: string; start: string; end: string; attendees: string; description: string } | null>(null);
  const proposed = (a: Approval) => { setProposal(a); setForm(null); void queryClient.invalidateQueries({ queryKey: ["approvals"] }); };
  const cancel = useMutation({ mutationFn: () => api.post<Approval>(`/calendar/events/${event!.id}/cancel-proposal`), onSuccess: proposed, onError: (e) => toast.error(errorMessage(e)) });
  const update = useMutation({
    mutationFn: () => api.post<Approval>(`/calendar/events/${event!.id}/update-proposal`, { title: form!.title, start_at: `${form!.start}:00`, end_at: `${form!.end}:00`, description: form!.description, location: event!.location, attendees: form!.attendees.split(",").map((s) => s.trim()).filter(Boolean) }),
    onSuccess: proposed, onError: (e) => toast.error(errorMessage(e)),
  });
  const close = () => { setProposal(null); setForm(null); onClose(); };
  const startEditing = () => setForm({ title: event!.title, start: toLocalInput(zonedDate(event!.start_at)), end: toLocalInput(zonedDate(event!.end_at)), attendees: event!.attendees.join(", "), description: event!.description });
  return (
    <Dialog open={!!event} onOpenChange={(o) => !o && close()}>
      {event && (
        <DialogContent title={event.title} description={`${fmtDateTime(event.start_at)} – ${fmtTime(event.end_at)}`}>
          <div className="space-y-2 text-[13px]">
            <div className="flex gap-1.5"><StatusBadge status={event.status === "proposed" ? "pending" : "completed"} label={event.status === "proposed" ? "Proposed" : "Confirmed"} />{event.is_demo && <SimulatedBadge label="Demo" />}</div>
            {event.description && <p className="whitespace-pre-wrap">{event.description}</p>}
            {event.location && <p className="text-muted-foreground">{event.location}</p>}
            {event.attendees.length > 0 && <p className="text-muted-foreground">With {event.attendees.join(", ")}</p>}
            {event.status === "proposed" && <Button size="sm" asChild><Link to="/approvals">Review in approvals</Link></Button>}
            {proposal && <ApprovalCard approval={proposal} onChanged={close} />}
            {form && (
              <form className="space-y-3 border-t pt-3" onSubmit={(e) => { e.preventDefault(); update.mutate(); }}>
                <Field label="Title" htmlFor="u-title"><Input id="u-title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Start" htmlFor="u-start"><Input id="u-start" type="datetime-local" required value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></Field>
                  <Field label="End" htmlFor="u-end"><Input id="u-end" type="datetime-local" required value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></Field>
                </div>
                <Field label="Attendees" hint="Email addresses, separated by commas" htmlFor="u-att"><Input id="u-att" value={form.attendees} onChange={(e) => setForm({ ...form, attendees: e.target.value })} /></Field>
                <Field label="Description" htmlFor="u-desc"><Textarea id="u-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
                <p className="text-xs text-muted-foreground">The event won't change until you approve this update.</p>
                <div className="flex justify-end gap-2"><Button size="sm" onClick={() => setForm(null)}>Cancel</Button><Button size="sm" variant="primary" type="submit" loading={update.isPending}>Propose update</Button></div>
              </form>
            )}
            {event.status === "confirmed" && !proposal && !form && <div className="flex justify-end gap-2"><Button size="sm" onClick={startEditing}>Edit</Button><Button size="sm" variant="danger" onClick={() => cancel.mutate()} loading={cancel.isPending}>Propose deletion</Button></div>}
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}

export function CalendarPage() {
  const { prefs, isDemo } = useAuth();
  const queryClient = useQueryClient();
  const tz = prefs?.timezone ?? "UTC";
  const [weekStart, setWeekStart] = useState(() => startOfWeek(zonedDate(new Date())));
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<CalendarEvent | null>(null);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);
  // Dates without an offset are read by the server in the user's saved timezone.
  const range = `start=${isoDate(weekStart)}T00:00:00&end=${isoDate(addDays(weekStart, 7))}T00:00:00`;
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["calendar", "events", range, tz], queryFn: () => api.get<CalendarEvent[]>(`/calendar/events?${range}`) });
  const sync = useMutation({ mutationFn: () => api.post<{ synced: number }>("/calendar/sync"), onSuccess: (r) => { toast.success(`Synced ${r.synced} event(s)`); void queryClient.invalidateQueries({ queryKey: ["calendar"] }); }, onError: (e) => toast.error(errorMessage(e)) });

  const today = isoDate(zonedDate(new Date()));
  const eventsFor = (day: Date) => (data ?? []).filter((e) => isoDate(zonedDate(e.start_at)) === isoDate(day));
  const title = `${days[0].toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;

  return (
    <>
      <PageHeader title="Calendar" description={`All times are in ${tz}. Change this in Settings.`} actions={<>{!isDemo && <Button onClick={() => sync.mutate()} loading={sync.isPending}><RefreshCw />Sync</Button>}<Button variant="primary" onClick={() => setCreating(true)}><Plus />New event</Button></>} />
      <Card>
        <div className="flex items-center gap-1 border-b px-3 py-2">
          <Button variant="ghost" size="icon" aria-label="Previous week" onClick={() => setWeekStart(addDays(weekStart, -7))}><ChevronLeft /></Button>
          <Button variant="ghost" size="icon" aria-label="Next week" onClick={() => setWeekStart(addDays(weekStart, 7))}><ChevronRight /></Button>
          <Button size="sm" onClick={() => setWeekStart(startOfWeek(zonedDate(new Date())))}>Today</Button>
          <span className="ml-2 text-[13px] font-semibold">{title}</span>
        </div>
        {isPending ? <RowsSkeleton rows={8} /> : isError ? <ErrorState error={error} onRetry={() => void refetch()} /> : (
          <>
            <div className="hidden md:block">
              <div className="grid grid-cols-[48px_repeat(7,minmax(0,1fr))] border-b">
                <div />
                {days.map((d) => <div key={d.toISOString()} className={cn("border-l px-2 py-1.5 text-center text-xs", isoDate(d) === today && "font-semibold text-primary")}>{d.toLocaleDateString(undefined, { weekday: "short" })} {d.getDate()}</div>)}
              </div>
              <div className="grid grid-cols-[48px_repeat(7,minmax(0,1fr))]" style={{ height: (END_HOUR - START_HOUR) * HOUR_PX }}>
                <div className="relative">{Array.from({ length: END_HOUR - START_HOUR }, (_, i) => <span key={i} className="absolute right-1.5 -translate-y-1/2 text-2xs text-muted-foreground" style={{ top: i * HOUR_PX }}>{i > 0 && `${String(START_HOUR + i).padStart(2, "0")}:00`}</span>)}</div>
                {days.map((d) => (
                  <div key={d.toISOString()} className="relative border-l">
                    {Array.from({ length: END_HOUR - START_HOUR }, (_, i) => <div key={i} className="border-b border-border/50" style={{ height: HOUR_PX }} />)}
                    {eventsFor(d).map((e) => {
                      const start = zonedDate(e.start_at), finish = zonedDate(e.end_at);
                      const top = Math.max(0, (start.getHours() + start.getMinutes() / 60 - START_HOUR) * HOUR_PX);
                      const height = Math.max(20, ((finish.getTime() - start.getTime()) / 3_600_000) * HOUR_PX - 2);
                      const proposed = e.status === "proposed";
                      return (
                        <button key={e.id} onClick={() => setSelected(e)} style={{ top, height }} className={cn("absolute inset-x-0.5 overflow-hidden rounded border-l-2 px-1.5 py-0.5 text-left text-2xs", proposed ? "border border-dashed border-warning/60 bg-warning/10" : "border-l-primary bg-primary/10")}>
                          <span className="block truncate font-medium">{e.title}</span>
                          <span className="block truncate text-muted-foreground">{fmtTime(e.start_at)}{proposed && " · proposed"}</span>
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
            <div className="divide-y md:hidden">
              {days.map((d) => {
                const list = eventsFor(d);
                return (
                  <div key={d.toISOString()} className="px-3 py-2">
                    <p className={cn("text-xs font-medium", isoDate(d) === today && "text-primary")}>{d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}</p>
                    {list.length === 0 ? <p className="text-xs text-muted-foreground">No events</p> : list.map((e) => <button key={e.id} onClick={() => setSelected(e)} className="mt-1 block w-full rounded bg-accent px-2 py-1 text-left text-[13px]">{fmtTime(e.start_at)} · {e.title}{e.status === "proposed" && " (proposed)"}</button>)}
                  </div>
                );
              })}
            </div>
            {data.length === 0 && <EmptyState compact icon={CalendarIcon} title="Nothing scheduled this week" />}
          </>
        )}
      </Card>
      <NewEventDialog open={creating} onOpenChange={setCreating} tz={tz} />
      <EventDialog event={selected} onClose={() => setSelected(null)} />
    </>
  );
}
