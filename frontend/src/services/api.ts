/** Thin fetch client: cookies for auth, CSRF header on writes, one error type. */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )ap_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

type Options = { method?: string; body?: unknown; form?: FormData; signal?: AbortSignal };

function buildInit({ method = "GET", body, form, signal }: Options): RequestInit {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return { method, headers, credentials: "same-origin", signal, body: form ?? (body !== undefined ? JSON.stringify(body) : undefined) };
}

async function toError(response: Response): Promise<ApiError> {
  try {
    const { error } = await response.json();
    return new ApiError(response.status, error.code, error.message, error.details);
  } catch {
    return new ApiError(response.status, "network_error", response.status >= 500 ? "The server is having trouble. Please try again." : "The request failed.");
  }
}

async function send(path: string, options: Options): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, buildInit(options));
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection and try again.");
  }
  if (!response.ok) {
    const failure = await toError(response);
    if (failure.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("autopilot:unauthorized"));
    throw failure;
  }
  return response;
}

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const response = await send(path, options);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body ?? {} }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body }),
  del: <T = void>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", form }),
};

export type StreamEvent = { type: string; [key: string]: any };

/** POST and read a server-sent event stream, calling onEvent for each event. */
export async function streamChat(path: string, body: unknown, onEvent: (event: StreamEvent) => void, signal?: AbortSignal): Promise<void> {
  const response = await send(path, { method: "POST", body, signal });
  const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = chunk.split("\n").filter((line) => line.startsWith("data: ")).map((line) => line.slice(6)).join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}
