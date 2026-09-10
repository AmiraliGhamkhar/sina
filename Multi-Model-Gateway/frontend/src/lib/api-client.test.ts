import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiClient, ApiError } from "./api-client";
import { useAuthStore } from "@/stores/auth-store";

/**
 * Behavior tests for the api-client transport:
 *  - the unified 401 → refresh → retry policy (authFetch), shared by both the
 *    JSON `request()` and binary `fetchBlob()` paths — the policy that used to
 *    be copy-pasted and drifted apart;
 *  - the coalesced refresh (one in-flight refresh for concurrent 401s);
 *  - SSE frame splitting across chunk boundaries for both the JSON stream
 *    (streamEvents) and the plain-token chat stream (streamChat).
 *
 * The public singleton reads its token sources from the (injectable) auth
 * store, and the transport calls the global `fetch`, which we stub per test.
 * So no internals are exposed — the tests drive and assert through the
 * public interface and the auth store's observable state.
 */

type Route =
  | Response
  | (() => Response | Promise<Response>);

/** Stub global fetch, routing by URL prefix. Returns the mock for assertions. */
function mockFetch(routes: Record<string, Route>) {
  const fn = vi.fn((input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    for (const [prefix, route] of Object.entries(routes)) {
      if (url.includes(prefix)) {
        return Promise.resolve(typeof route === "function" ? route() : route);
      }
    }
    return Promise.resolve(new Response('{"detail":"not stubbed"}', { status: 500 }));
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const refreshOk = (access = "new-access", refresh = "new-refresh"): Route =>
  json({ access_token: access, refresh_token: refresh });

/** A 200 SSE Response that streams its chunks with a real byte boundary. */
function sseResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(enc.encode(chunk));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  useAuthStore.setState({
    isAuthenticated: true,
    accessToken: "access-1",
    refreshToken: "refresh-1",
    userEmail: "u@x.io",
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.setState({
    isAuthenticated: false,
    accessToken: null,
    refreshToken: null,
    userEmail: null,
  });
});

describe("ApiClient", () => {
  it("exposes the singleton and classifies errors", () => {
    expect(apiClient).toBeDefined();
    expect(new ApiError(401, "x").isAuthError).toBe(true);
    expect(new ApiError(429, "x").isRateLimit).toBe(true);
    expect(new ApiError(404, "x").isNotFound).toBe(true);
    expect(new ApiError(0, "x").isNetworkError).toBe(true);
    expect(new ApiError(500, "x").isNotFound).toBe(false);
  });
});

describe("request() refresh-retry policy (authFetch)", () => {
  it("refreshes once on 401 and retries, persisting the rotated tokens", async () => {
    let convoCalls = 0;
    mockFetch({
      "/auth/refresh": refreshOk(),
      "/v1/convo": () =>
        convoCalls++ === 0
          ? json({ detail: "expired" }, 401)
          : json({ data: [{ id: "c1" }] }),
    });

    const result = await apiClient.request<{ data: { id: string }[] }>(
      "GET",
      "/v1/convo"
    );

    expect(result.data).toEqual([{ id: "c1" }]);
    // Rotated tokens came back through onRefresh → setTokens.
    expect(useAuthStore.getState().accessToken).toBe("new-access");
    expect(useAuthStore.getState().refreshToken).toBe("new-refresh");
  });

  it("signs the user out when the retried request STILL 401s (the drift fix)", async () => {
    const fetchMock = mockFetch({
      "/auth/refresh": refreshOk(),
      "/v1/convo": json({ detail: "still expired" }, 401),
    });

    await expect(apiClient.request("GET", "/v1/convo")).rejects.toThrow(
      ApiError
    );
    // The previous drift: request() fell through to a plain error with no
    // onAuthFailure. Now a surviving 401 must clear the session.
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
    // Refresh was attempted exactly once for this one request.
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/auth/refresh"))).toHaveLength(1);
  });

  it("signs out an authenticated session with no refresh token on 401", async () => {
    useAuthStore.setState({ accessToken: "access-only" });
    mockFetch({ "/v1/convo": json({ detail: "expired" }, 401) });

    await expect(apiClient.request("GET", "/v1/convo")).rejects.toThrow(ApiError);
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("does NOT sign out an anonymous call (no token attached) on 401", async () => {
    useAuthStore.setState({ accessToken: null, refreshToken: null });
    mockFetch({ "/auth/login": json({ detail: "invalid credentials" }, 401) });

    await expect(
      apiClient.request("POST", "/auth/login", { email: "a", password: "b" })
    ).rejects.toThrow(ApiError);
    // Anonymous 401 is "wrong credentials" — onAuthFailure must NOT have
    // fired, so the (already-true) auth flag is untouched by the request.
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("coalesces concurrent refresh attempts into a single in-flight request", async () => {
    let refreshCount = 0;
    let releaseRefresh!: (r: Response) => void;
    const gate = new Promise<Response>((res) => (releaseRefresh = res));

    mockFetch({
      "/auth/refresh": () => {
        refreshCount += 1;
        return gate.then(() => refreshOk()());
      },
      "/v1/convo": json({ detail: "expired" }, 401),
    });

    const p1 = apiClient.request("GET", "/v1/convo").catch(() => null);
    // Wait until the first request's refresh has actually started (still
    // gated, so refreshInFlight is held open).
    await vi.waitFor(() => expect(refreshCount).toBe(1));
    const p2 = apiClient.request("GET", "/v1/convo").catch(() => null);

    releaseRefresh(json({ access_token: "tok-2", refresh_token: "ref-2" }));
    await Promise.all([p1, p2]);

    // Both requests rode the same refresh primitive → exactly one call.
    expect(refreshCount).toBe(1);
  });
});

describe("fetchBlob() shares the same policy", () => {
  it("signs out when a blob fetch 401s with no refresh token", async () => {
    useAuthStore.setState({ accessToken: "access-only" });
    mockFetch({ "/v1/images/file": json({ detail: "expired" }, 401) });

    await expect(apiClient.fetchBlob("/v1/images/file?x=1")).rejects.toThrow(
      ApiError
    );
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("recovers via refresh and returns the blob + content type", async () => {
    let fileCalls = 0;
    mockFetch({
      "/auth/refresh": refreshOk(),
      "/v1/images/file": () =>
        fileCalls++ === 0
          ? json({ detail: "expired" }, 401)
          : new Response(new Blob(["\x89PNG"]), {
              status: 200,
              headers: { "Content-Type": "image/png" },
            }),
    });

    const { blob, contentType } = await apiClient.fetchBlob("/v1/images/file?x=1");
    expect(contentType).toBe("image/png");
    expect(blob.size).toBeGreaterThan(0);
    expect(useAuthStore.getState().accessToken).toBe("new-access");
  });
});

describe("streamEvents() SSE frame splitting", () => {
  it("yields JSON events split across arbitrary chunk boundaries", async () => {
    mockFetch({
      "/v1/agent/chat": sseResponse([
        'data: {"type":"token","content":"he',
        'llo"}\n\ndata: {"type":"tool_call","id":"t1","name":"w","arguments":"{}"}\n',
        '\ndata: {"type":"done","conversation_id":"c9"}\n\n',
      ]),
    });

    const events = [];
    for await (const ev of apiClient.streamEvents("POST", "/v1/agent/chat", {})) {
      events.push(ev);
    }

    expect(events).toEqual([
      { type: "token", content: "hello" },
      { type: "tool_call", id: "t1", name: "w", arguments: "{}" },
      { type: "done", conversation_id: "c9" },
    ]);
  });
});

describe("streamChat() plain-token chat stream", () => {
  it("delivers tokens then [DONE] across chunk boundaries", async () => {
    mockFetch({
      "/v1/chat/completions": sseResponse([
        "data: Hello",
        " world\n\ndata: !\n\ndata: [DONE]\n\n",
      ]),
    });

    const tokens: string[] = [];
    let done = false;
    let error: string | null = null;

    await apiClient.streamChat(
      { conversation_id: "c1", messages: [{ role: "user", content: "hi" }], model: "m", stream: true },
      (t) => tokens.push(t),
      () => (done = true),
      (e) => (error = e)
    );

    expect(error).toBeNull();
    expect(done).toBe(true);
    expect(tokens).toEqual(["Hello world", "!"]);
  });
});
