import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CalendarDays, FolderGit2, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ErrorState, PageHeader, RowsSkeleton, SimulatedBadge } from "@/components/common";
import { Badge, Button, Card, CardHeader, ConfirmDialog, Input, Switch } from "@/components/ui";
import { useAuth } from "@/contexts";
import { api } from "@/services/api";
import type { IntegrationsInfo, Preferences, SystemStatus, User } from "@/types";
import { errorMessage, fmtDate } from "@/utils";

const GOOGLE_RESULT: Record<string, [("success" | "error"), string]> = {
  connected: ["success", "Google account connected."],
  denied: ["error", "Google access was not granted."],
  invalid_state: ["error", "That sign-in link expired or was invalid. Please try again."],
  exchange_failed: ["error", "Google didn't accept the sign-in. Please try again."],
};

const Health = ({ ok, label }: { ok: boolean; label: string }) => <span className="flex items-center gap-1.5 text-xs"><span className={`size-2 rounded-full ${ok ? "bg-success" : "bg-danger"}`} />{label}: {ok ? "OK" : "Unavailable"}</span>;

export function IntegrationsPage() {
  const queryClient = useQueryClient();
  const { user, setUser } = useAuth();
  const [params, setParams] = useSearchParams();
  const [token, setToken] = useState("");
  const [confirm, setConfirm] = useState(false);
  const { data, isPending, isError, error, refetch } = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<IntegrationsInfo>("/integrations") });
  const system = useQuery({ queryKey: ["system"], queryFn: () => api.get<SystemStatus>("/system/status") });
  const drive = useQuery({ queryKey: ["drive"], queryFn: () => api.get<{ files: { file_id: string; name: string; modified_at: string | null }[] }>("/integrations/google/drive/files"), enabled: data?.mode === "live" && data.google.connected });
  const issues = useQuery({ queryKey: ["github"], queryFn: () => api.get<{ issues: { id: number; title: string; url: string; repository: string }[] }>("/integrations/github/issues"), enabled: !!data?.github.connected });

  useEffect(() => {
    const result = params.get("google");
    if (!result) return;
    const entry = GOOGLE_RESULT[result];
    if (entry) toast[entry[0]](entry[1]);
    setParams({}, { replace: true });
  }, [params, setParams]);

  const refresh = () => { for (const k of ["integrations", "emails", "calendar", "dashboard", "tasks", "documents"]) void queryClient.invalidateQueries({ queryKey: [k] }); };
  const fail = (e: unknown) => toast.error(errorMessage(e));
  const connect = useMutation({ mutationFn: () => api.get<{ url: string }>("/integrations/google/start"), onSuccess: ({ url }) => { window.location.href = url; }, onError: fail });
  const disconnect = useMutation({ mutationFn: () => api.post("/integrations/google/disconnect"), onSuccess: () => { setConfirm(false); toast.success("Google disconnected. Back in demo mode."); void queryClient.invalidateQueries({ queryKey: ["me"] }); refresh(); }, onError: fail });
  const mode = useMutation({ mutationFn: (live: boolean) => api.put<Preferences>("/settings/preferences", { integration_mode: live ? "live" : "demo" }), onSuccess: (preferences) => { if (user) setUser({ ...user, preferences } as User); refresh(); }, onError: fail });
  const github = useMutation({ mutationFn: () => api.post("/integrations/github", { token }), onSuccess: () => { setToken(""); toast.success("GitHub connected"); refresh(); }, onError: fail });
  const githubOff = useMutation({ mutationFn: () => api.del("/integrations/github"), onSuccess: refresh, onError: fail });
  const importFile = useMutation({ mutationFn: (file_id: string) => api.post("/integrations/google/drive/import", { file_id }), onSuccess: () => { toast.success("Imported to Documents"); refresh(); }, onError: fail });

  if (isPending) return <RowsSkeleton rows={6} />;
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />;
  const { google } = data;
  return (
    <>
      <PageHeader title="Integrations" description="Connect your services, or keep exploring with simulated data." />
      <Card className="mb-4 flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-[13px] font-semibold">Live integrations {data.mode === "demo" && <SimulatedBadge label="Demo mode on" />}</p>
          <p className="text-xs text-muted-foreground">{data.mode === "demo" ? "Email, calendar and sharing are simulated. Nothing leaves AutoPilot." : "Approved actions run against your real Google account."}</p>
        </div>
        <Switch aria-label="Use live integrations" checked={data.mode === "live"} disabled={!google.connected || mode.isPending} onCheckedChange={(v) => mode.mutate(v)} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Google" description="Gmail, Google Calendar and Google Drive" action={google.connected ? <Badge tone={google.status === "connected" ? "success" : "danger"}>{google.status === "connected" ? "Connected" : "Reconnect needed"}</Badge> : <Badge>Not connected</Badge>} />
          <div className="space-y-3 p-4">
            <div className="flex gap-3 text-xs text-muted-foreground"><span className="flex items-center gap-1"><Mail className="size-3.5" />Gmail</span><span className="flex items-center gap-1"><CalendarDays className="size-3.5" />Calendar</span><span className="flex items-center gap-1"><FolderGit2 className="size-3.5" />Drive</span></div>
            {google.account_email && <p className="text-[13px]">{google.account_email}</p>}
            {!google.configured && <p className="rounded bg-warning/10 px-2 py-1.5 text-xs text-warning">Not configured on this server. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET; see the README.</p>}
            <div className="flex gap-2">
              {(!google.connected || google.status !== "connected") && <Button variant="primary" disabled={!google.configured} loading={connect.isPending} onClick={() => connect.mutate()}>{google.connected ? "Reconnect" : "Connect Google"}</Button>}
              {google.connected && <Button variant="danger" onClick={() => setConfirm(true)}>Disconnect</Button>}
            </div>
            {drive.data && drive.data.files.length > 0 && (
              <div><p className="mb-1 text-xs font-medium">Recent Drive files</p>
                <ul className="divide-y rounded border">{drive.data.files.slice(0, 8).map((f) => <li key={f.file_id} className="flex items-center gap-2 px-2 py-1 text-[13px]"><span className="min-w-0 flex-1 truncate">{f.name}</span><span className="text-xs text-muted-foreground">{f.modified_at && fmtDate(f.modified_at)}</span><Button size="sm" onClick={() => importFile.mutate(f.file_id)}>Import</Button></li>)}</ul>
              </div>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="GitHub" description="Optional. Read-only view of issues assigned to you" action={data.github.connected ? <Badge tone="success">Connected</Badge> : <Badge>Not connected</Badge>} />
          <div className="space-y-3 p-4">
            {data.github.connected ? (
              <>
                <p className="text-[13px]">Signed in as {data.github.account}</p>
                {issues.isError && <p className="text-xs text-danger">{errorMessage(issues.error)}</p>}
                {issues.data && (issues.data.issues.length ? <ul className="space-y-1 text-[13px]">{issues.data.issues.slice(0, 6).map((i) => <li key={i.id} className="truncate"><a className="hover:underline" href={i.url} target="_blank" rel="noreferrer noopener">{i.title}</a> <span className="text-xs text-muted-foreground">{i.repository}</span></li>)}</ul> : <p className="text-xs text-muted-foreground">No open issues assigned to you.</p>)}
                <Button variant="danger" loading={githubOff.isPending} onClick={() => githubOff.mutate()}>Disconnect</Button>
              </>
            ) : (
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); github.mutate(); }}>
                <Input type="password" aria-label="GitHub personal access token" placeholder="Personal access token" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
                <Button type="submit" disabled={token.length < 20} loading={github.isPending}>Connect</Button>
              </form>
            )}
            <p className="text-xs text-muted-foreground">Tokens are encrypted at rest and never shown again.</p>
          </div>
        </Card>

        <Card>
          <CardHeader title={<span className="flex items-center gap-1.5"><Bot className="size-3.5" />AI provider</span>} description="Configured on the server" action={data.ai.simulated ? <SimulatedBadge label="Rule-based demo AI" /> : <Badge tone="success">Live model</Badge>} />
          <div className="space-y-1 p-4 text-[13px]">
            <p>Provider: <span className="font-medium">{data.ai.provider}</span></p>
            <p>Model: <span className="font-medium">{data.ai.model}</span></p>
            {data.ai.simulated && <p className="text-xs text-muted-foreground">Set LLM_PROVIDER and an API key to use a real model. The demo AI runs through the same tools and approvals, using keyword rules.</p>}
            {system.data?.ai.message && <p className="text-xs text-danger">{system.data.ai.message}</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="System health" />
          {system.data ? <div className="grid grid-cols-2 gap-2 p-4"><Health ok={system.data.database} label="Database" /><Health ok={system.data.redis} label="Queue" /><Health ok={system.data.worker_online} label="Worker" /><Health ok={system.data.ai.ready} label="AI" /></div> : <RowsSkeleton rows={2} />}
        </Card>
      </div>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Disconnect Google?" description="AutoPilot will revoke its access and return to demo mode. Previously synced data stays until you remove it." confirmLabel="Disconnect" loading={disconnect.isPending} onConfirm={() => disconnect.mutate()} />
    </>
  );
}
