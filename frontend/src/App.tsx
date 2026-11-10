import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { RowsSkeleton } from "@/components/common";
import { useAuth } from "@/contexts";
import { AppLayout } from "@/layouts/AppLayout";
import { AIEmployeePage } from "@/pages/AIEmployee";
import { LoginPage, RegisterPage } from "@/pages/Auth";
import { AutomationsPage } from "@/pages/Automations";
import { CalendarPage } from "@/pages/Calendar";
import { DocumentsPage } from "@/pages/Documents";
import { InboxPage } from "@/pages/Inbox";
import { IntegrationsPage } from "@/pages/Integrations";
import { ActivityPage, ApprovalsPage, NotFoundPage } from "@/pages/Misc";
import { OverviewPage } from "@/pages/Overview";
import { SettingsPage } from "@/pages/Settings";
import { TasksPage } from "@/pages/Tasks";

const AnalyticsPage = lazy(() => import("@/pages/Analytics").then((m) => ({ default: m.AnalyticsPage })));

function Protected({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user === undefined) return <div className="p-6"><RowsSkeleton /></div>;
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

function PublicOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user === undefined) return null;
  return user ? <Navigate to="/" replace /> : <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<PublicOnly><LoginPage /></PublicOnly>} />
      <Route path="/register" element={<PublicOnly><RegisterPage /></PublicOnly>} />
      <Route element={<Protected><AppLayout /></Protected>}>
        <Route index element={<OverviewPage />} />
        <Route path="ai" element={<AIEmployeePage />} />
        <Route path="ai/:id" element={<AIEmployeePage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="inbox/:id" element={<InboxPage />} />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:id" element={<DocumentsPage />} />
        <Route path="automations" element={<AutomationsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="analytics" element={<Suspense fallback={<RowsSkeleton />}><AnalyticsPage /></Suspense>} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
