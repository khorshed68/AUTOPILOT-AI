import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, BarChart3, Bell, Bot, Calendar, CheckSquare, FileText, Inbox, LayoutDashboard, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Pause, Play, Plug, Search, Settings, ShieldCheck, Sun, Workflow, X, type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Badge, Button, DropdownContent, DropdownItem, DropdownMenu, DropdownSeparator, DropdownTrigger } from "@/components/ui";
import { SimulatedBadge } from "@/components/common";
import { useAuth, useTheme } from "@/contexts";
import { api } from "@/services/api";
import type { EmployeeState, EmployeeStatus, NotificationItem, Page, SearchResult } from "@/types";
import { cn, errorMessage, initials, plural, timeAgo, titleCase } from "@/utils";

const NAV: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/ai", label: "AI Employee", icon: Bot },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
  { to: "/calendar", label: "Calendar", icon: Calendar },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/automations", label: "Automations", icon: Workflow },
  { to: "/activity", label: "Activity History", icon: Activity },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/integrations", label: "Integrations", icon: Plug },
  { to: "/settings", label: "Settings", icon: Settings },
];

const STATE: Record<EmployeeState, { label: string; dot: string }> = {
  online: { label: "Online", dot: "bg-success" },
  working: { label: "Working", dot: "bg-primary animate-pulse" },
  waiting_for_approval: { label: "Waiting for approval", dot: "bg-warning" },
  paused: { label: "Paused", dot: "bg-muted-foreground" },
  error: { label: "Error", dot: "bg-danger" },
};

function Logo({ collapsed }: { collapsed?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <img src="/favicon.svg" alt="" className="size-6" />
      {!collapsed && <span>AutoPilot AI</span>}
    </Link>
  );
}

function NavItems({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  return (
    <nav className="space-y-0.5" aria-label="Main">
      {NAV.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === "/"} onClick={onNavigate} title={collapsed ? label : undefined}
          className={({ isActive }) => cn("flex h-8 items-center gap-2.5 rounded-md px-2 text-[13px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground", isActive && "bg-accent text-foreground", collapsed && "justify-center")}>
          <Icon className="size-4 shrink-0" />
          {!collapsed && <span className="truncate">{label}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

function GlobalSearch() {
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { const t = setTimeout(() => setTerm(query.trim()), 200); return () => clearTimeout(t); }, [query]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const { data, isFetching } = useQuery({ queryKey: ["search", term], queryFn: () => api.get<{ results: SearchResult[] }>(`/search?q=${encodeURIComponent(term)}`), enabled: term.length >= 2 });
  return (
    <div ref={box} className="relative w-full max-w-sm">
      <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted-foreground" />
      <input aria-label="Search" value={query} onChange={(e) => { setQuery(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)} placeholder="Search tasks, email, documents…" className="h-8 w-full rounded-md border border-input bg-surface pl-8 pr-2 text-[13px] placeholder:text-muted-foreground" />
      {open && term.length >= 2 && (
        <div className="absolute z-40 mt-1 max-h-80 w-full overflow-y-auto rounded-md border bg-popover p-1 shadow-pop">
          {data?.results.length ? data.results.map((r) => (
            <button key={`${r.type}-${r.id}`} className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[13px] hover:bg-accent" onClick={() => { setOpen(false); setQuery(""); navigate(r.link); }}>
              <Badge className="shrink-0">{titleCase(r.type)}</Badge>
              <span className="truncate">{r.title}</span>
              <span className="ml-auto shrink-0 truncate text-xs text-muted-foreground">{r.subtitle}</span>
            </button>
          )) : <p className="px-2 py-3 text-center text-xs text-muted-foreground">{isFetching ? "Searching…" : "No results"}</p>}
        </div>
      )}
    </div>
  );
}

function Notifications() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { data: count } = useQuery({ queryKey: ["notifications", "count"], queryFn: () => api.get<{ count: number }>("/notifications/unread-count"), refetchInterval: 20_000 });
  const { data } = useQuery({ queryKey: ["notifications", "list"], queryFn: () => api.get<Page<NotificationItem>>("/notifications?page_size=12") });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const read = useMutation({ mutationFn: (id: string) => api.put(`/notifications/${id}/read`, {}), onSuccess: refresh });
  const readAll = useMutation({ mutationFn: () => api.post("/notifications/read-all"), onSuccess: refresh });
  const unread = count?.count ?? 0;
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Notifications, ${unread} unread`} className="relative">
          <Bell className="!size-4" />
          {unread > 0 && <span className="absolute right-0.5 top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-primary px-0.5 text-[9px] font-semibold text-primary-foreground">{unread > 9 ? "9+" : unread}</span>}
        </Button>
      </DropdownTrigger>
      <DropdownContent className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-[13px] font-semibold">Notifications</span>
          {unread > 0 && <button className="text-xs text-primary" onClick={() => readAll.mutate()}>Mark all read</button>}
        </div>
        <div className="max-h-96 overflow-y-auto p-1">
          {data?.items.length ? data.items.map((n) => (
            <DropdownItem key={n.id} className="items-start" onSelect={() => { if (!n.read_at) read.mutate(n.id); if (n.link) navigate(n.link); }}>
              <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-primary")} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{n.title}</span>
                {n.body && <span className="line-clamp-2 block text-xs text-muted-foreground">{n.body}</span>}
                <span className="mt-0.5 flex items-center gap-1.5 text-2xs text-muted-foreground">{timeAgo(n.deliver_at)}{n.is_demo && <SimulatedBadge label="Demo" />}</span>
              </span>
            </DropdownItem>
          )) : <p className="px-3 py-8 text-center text-xs text-muted-foreground">You're all caught up.</p>}
        </div>
      </DropdownContent>
    </DropdownMenu>
  );
}

function EmployeePill() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["employee"], queryFn: () => api.get<EmployeeStatus>("/employee/status"), refetchInterval: 8_000 });
  const toggle = useMutation({
    mutationFn: (paused: boolean) => api.post<EmployeeStatus>("/employee/pause", { paused }),
    onSuccess: (status) => queryClient.setQueryData(["employee"], status),
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (!data) return null;
  const state = STATE[data.state];
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="secondary" aria-label={`AI employee status: ${state.label}`}>
          <span className={cn("size-2 rounded-full", state.dot)} />
          <span className="hidden sm:inline">{state.label}</span>
        </Button>
      </DropdownTrigger>
      <DropdownContent className="w-64">
        <p className="px-2 py-1.5 text-xs text-muted-foreground">{data.detail}</p>
        <DropdownSeparator />
        {data.pending_approvals > 0 && <DropdownItem asChild><Link to="/approvals"><ShieldCheck />Review {plural(data.pending_approvals, "approval")}</Link></DropdownItem>}
        <DropdownItem onSelect={() => toggle.mutate(data.state !== "paused")}>{data.state === "paused" ? <><Play />Resume</> : <><Pause />Pause AI employee</>}</DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  const dark = theme === "dark" || (theme === "system" && document.documentElement.classList.contains("dark"));
  return <Button variant="ghost" size="icon" aria-label={dark ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setTheme(dark ? "light" : "dark")}>{dark ? <Sun className="!size-4" /> : <Moon className="!size-4" />}</Button>;
}

function ProfileMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  return (
    <DropdownMenu>
      <DropdownTrigger aria-label="Profile menu" className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{initials(user.full_name)}</DropdownTrigger>
      <DropdownContent className="w-56">
        <div className="px-2 py-1.5">
          <p className="truncate text-[13px] font-medium">{user.full_name}</p>
          <p className="truncate text-xs text-muted-foreground">{user.email}</p>
        </div>
        <DropdownSeparator />
        <DropdownItem onSelect={() => navigate("/settings")}><Settings />Settings</DropdownItem>
        <DropdownItem onSelect={() => { logout().catch((e) => toast.error(errorMessage(e))); }}><LogOut />Sign out</DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("autopilot-sidebar") === "1"; } catch { return false; } });
  const [mobileOpen, setMobileOpen] = useState(false);
  const { isDemo } = useAuth();
  const location = useLocation();
  const fullBleed = location.pathname.startsWith("/ai");
  useEffect(() => { try { localStorage.setItem("autopilot-sidebar", collapsed ? "1" : "0"); } catch { /* optional */ } }, [collapsed]);
  const { data: pending } = useQuery({ queryKey: ["approvals", "count"], queryFn: () => api.get<Page<unknown>>("/approvals?status=pending&page_size=1"), refetchInterval: 15_000 });

  return (
    <div className="flex h-full">
      <aside className={cn("hidden shrink-0 flex-col border-r bg-surface transition-[width] duration-150 md:flex", collapsed ? "w-14" : "w-56")}>
        <div className={cn("flex h-12 items-center border-b px-3", collapsed ? "justify-center" : "justify-between")}>
          <Logo collapsed={collapsed} />
          {!collapsed && <Button variant="ghost" size="icon" className="size-6" aria-label="Collapse sidebar" onClick={() => setCollapsed(true)}><PanelLeftClose /></Button>}
        </div>
        <div className="flex-1 overflow-y-auto p-2"><NavItems collapsed={collapsed} /></div>
        {collapsed && <div className="p-2"><Button variant="ghost" size="icon" className="w-full" aria-label="Expand sidebar" onClick={() => setCollapsed(false)}><PanelLeftOpen /></Button></div>}
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-60 border-r bg-surface p-3">
            <div className="mb-3 flex items-center justify-between"><Logo /><Button variant="ghost" size="icon" aria-label="Close navigation" onClick={() => setMobileOpen(false)}><X /></Button></div>
            <NavItems onNavigate={() => setMobileOpen(false)} />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 md:px-4">
          <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><Menu className="!size-4" /></Button>
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-1.5">
            {isDemo && <span className="hidden lg:inline-flex"><SimulatedBadge label="Demo mode" /></span>}
            {(pending?.total ?? 0) > 0 && <Button asChild variant="secondary"><Link to="/approvals"><ShieldCheck /><span className="hidden sm:inline">Approvals</span><Badge tone="warning">{pending!.total}</Badge></Link></Button>}
            <EmployeePill />
            <Notifications />
            <ThemeSwitcher />
            <ProfileMenu />
          </div>
        </header>
        <main className={cn("min-h-0 flex-1", fullBleed ? "overflow-hidden" : "overflow-y-auto")}>
          {fullBleed ? <Outlet /> : <div className="mx-auto max-w-6xl p-4 md:p-6"><Outlet /></div>}
        </main>
      </div>
    </div>
  );
}
