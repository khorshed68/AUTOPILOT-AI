export interface Preferences {
  timezone: string;
  theme: "light" | "dark" | "system";
  integration_mode: "demo" | "live";
  communication_tone: string;
  employee_paused: boolean;
  memory_enabled: boolean;
  memory_auto_create: boolean;
  work_start_hour: number;
  work_end_hour: number;
  notification_prefs: Record<string, boolean | number>;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  created_at: string;
  preferences: Preferences;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export type EmployeeState = "online" | "working" | "waiting_for_approval" | "paused" | "error";
export interface EmployeeStatus {
  state: EmployeeState;
  detail: string;
  active_jobs: number;
  pending_approvals: number;
}

export interface Email {
  id: string;
  sender_name: string | null;
  sender_email: string;
  subject: string;
  snippet: string;
  received_at: string;
  category: string | null;
  importance: "low" | "normal" | "high";
  ai_summary: string | null;
  suggested_action: string | null;
  action_items: string[];
  deadline_at: string | null;
  deadline_text: string | null;
  needs_reply: boolean;
  is_read: boolean;
  is_archived: boolean;
  injection_flags: string[];
  analyzed_at: string | null;
  is_demo: boolean;
  body?: string;
  recipients?: string[];
}

export type TaskStatus = "pending" | "in_progress" | "waiting" | "completed" | "cancelled";
export type Priority = "critical" | "high" | "medium" | "low";
export interface Task {
  id: string;
  title: string;
  description: string;
  priority: Priority;
  status: TaskStatus;
  category: string | null;
  progress: number;
  due_at: string | null;
  completed_at: string | null;
  source: string;
  related_email_id: string | null;
  related_document_id: string | null;
  is_demo: boolean;
  created_at: string;
  updated_at: string;
  overdue: boolean;
}

export interface CalendarEvent {
  id: string;
  title: string;
  description: string;
  location: string | null;
  start_at: string;
  end_at: string;
  timezone: string;
  attendees: string[];
  status: string;
  source: string;
  is_demo: boolean;
}

export interface DocumentItem {
  id: string;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  kind: "upload" | "report" | "drive";
  status: "ready" | "failed";
  error: string | null;
  analysis: { summary: string; key_points: string[]; topics: string[]; dates: { text: string; date: string }[]; simulated_ai?: boolean } | null;
  sources: { id: string; title: string }[];
  injection_flags: string[];
  is_demo: boolean;
  created_at: string;
  updated_at: string;
  content?: string;
}

export type ApprovalStatus = "pending" | "executing" | "executed" | "failed" | "rejected" | "expired" | "outcome_unknown";
export interface Approval {
  id: string;
  action_type: string;
  title: string;
  description: string;
  payload: Record<string, any>;
  payload_hash: string;
  version: number;
  status: ApprovalStatus;
  requested_by: string;
  origin: string;
  conversation_id: string | null;
  derived_from_untrusted: boolean;
  simulated: boolean;
  result: Record<string, any> | null;
  error: string | null;
  expires_at: string;
  decided_at: string | null;
  executed_at: string | null;
  created_at: string;
}

export interface StepResult {
  agent: string;
  task_id: string;
  status: "completed" | "needs_input" | "failed" | "skipped";
  summary: string;
  question: string | null;
  errors: string[];
  duration_ms: number;
}

export interface PlanInfo {
  objective: string;
  intent: string;
  steps: { id: string; agent: string; goal: string; depends_on: string[] }[];
}

export interface LedgerEntry {
  kind: "done" | "approval";
  title: string;
  agent: string;
  approval_id?: string;
  status?: string;
  entity?: string;
  entity_id?: string;
}

export interface MessageMeta {
  simulated_ai?: boolean;
  plan?: PlanInfo;
  steps?: StepResult[];
  ledger?: LedgerEntry[];
  approval_ids?: string[];
  tainted?: boolean;
  memories_saved?: string[];
  error?: { message: string; retryable: boolean };
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  status: "complete" | "streaming" | "error";
  meta: MessageMeta;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface AutomationStep {
  id?: string;
  position?: number;
  action_type: string;
  config: Record<string, any>;
}

export interface Condition {
  field: string;
  op: string;
  value?: string;
}

export interface Automation {
  id: string;
  name: string;
  description: string;
  trigger_type: "manual" | "daily" | "weekly" | "new_email";
  trigger_config: Record<string, any>;
  conditions: Condition[];
  condition_logic: "all" | "any";
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  is_demo: boolean;
  steps: AutomationStep[];
  created_at: string;
}

export interface Execution {
  id: string;
  automation_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "skipped";
  trigger: string;
  is_test: boolean;
  step_results: { position: number; action_type: string; label: string; status: string; output?: Record<string, any>; error?: string; duration_ms: number }[];
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  is_demo: boolean;
  created_at: string;
}

export interface AutomationCatalog {
  triggers: { type: string; label: string; description: string; config?: string[] }[];
  condition_fields: { field: string; label: string; kind: "text" | "boolean" }[];
  text_operators: string[];
  boolean_operators: string[];
  actions: { type: string; label: string; requires: string | null; agent: string; description: string; config?: string[] }[];
}

export interface NotificationItem {
  id: string;
  kind: string;
  title: string;
  body: string;
  link: string | null;
  deliver_at: string;
  read_at: string | null;
  is_demo: boolean;
}

export interface ActivityItem {
  id: string;
  actor: string;
  action: string;
  description: string;
  entity_type: string | null;
  entity_id: string | null;
  status: "success" | "failure" | "info" | "pending";
  simulated: boolean;
  created_at: string;
  meta: Record<string, any>;
}

export interface Memory {
  id: string;
  kind: string;
  content: string;
  source: "user" | "ai";
  last_used_at: string | null;
  created_at: string;
}

export interface Dashboard {
  stats: Record<"total_tasks" | "completed_tasks" | "pending_tasks" | "overdue_tasks" | "emails_processed" | "upcoming_deadlines" | "active_automations", number>;
  simulated: boolean;
  employee: EmployeeStatus;
  timezone: string;
  important_emails: { id: string; subject: string; sender: string; category: string | null; summary: string | null; received_at: string; deadline_at: string | null; is_demo: boolean }[];
  pending_tasks: { id: string; title: string; priority: Priority; status: string; due_at: string | null; overdue: boolean; is_demo: boolean }[];
  upcoming_events: { id: string; title: string; start_at: string; end_at: string; attendees: string[]; is_demo: boolean }[];
  recent_activity: ActivityItem[];
  recommendations: { title: string; reason: string; link: string; prompt: string | null }[];
}

export interface Analytics {
  days: number;
  simulated: boolean;
  totals: { emails_processed: number; tasks_completed: number; tasks_pending: number; automation_success_rate: number | null; automation_failure_rate: number | null; automation_runs: number; avg_execution_ms: number | null };
  time_saved: { minutes_estimate: number; is_estimate: boolean; assumed_minutes_per_action: Record<string, number>; counts: Record<string, number>; explanation: string };
  agents: { agent: string; runs: number; avg_ms: number }[];
  task_trend: { date: string; created: number; completed: number }[];
  weekly_productivity: { week: string; completed: number }[];
  email_categories: { category: string; count: number }[];
  automation_history: { date: string; succeeded: number; failed: number }[];
}

export interface IntegrationsInfo {
  mode: "demo" | "live";
  google: { configured: boolean; connected: boolean; status: string | null; account_email: string | null; scopes: string[]; services: string[] };
  github: { connected: boolean; account: string | null };
  ai: { provider: string; model: string; simulated: boolean };
}

export interface SystemStatus {
  database: boolean;
  redis: boolean;
  worker_online: boolean;
  ai: { provider: string; model: string; simulated: boolean; ready: boolean; message?: string };
  demo_mode_enabled: boolean;
  environment: string;
}

export interface SearchResult {
  type: string;
  id: string;
  title: string;
  subtitle: string;
  link: string;
}
