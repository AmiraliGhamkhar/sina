import type { ChatRequest } from "./types";
import { useAuthStore } from "@/stores/auth-store";
import { getDeviceId } from "@/lib/device-id";
import { interpretChatFrame, parseSseJson } from "./sse-events";

export class ApiError extends Error {
  statusCode: number;
  detail: string;

  constructor(statusCode: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.statusCode = statusCode;
    this.detail = detail;
  }

  get isAuthError() {
    return this.statusCode === 401;
  }
  get isRateLimit() {
    return this.statusCode === 429;
  }
  get isNotFound() {
    return this.statusCode === 404;
  }
  get isNetworkError() {
    return this.statusCode === 0;
  }
}

const NETWORK_MESSAGE =
  "Cannot connect to server. Check if the backend is running.";

interface ApiClientConfig {
  baseUrl: string;
  apiPrefix: string;
  getAccessToken: () => string | null;
  getRefreshToken: () => string | null;
  onRefresh: (accessToken: string, refreshToken: string) => void;
  onAuthFailure: () => void;
}

class ApiClient {
  private baseUrl: string;
  private apiPrefix: string;
  private getAccessToken: () => string | null;
  private getRefreshToken: () => string | null;
  private onRefresh: (accessToken: string, refreshToken: string) => void;
  private onAuthFailure: () => void;

  // Coalesce concurrent refreshes into a single in-flight request.
  private refreshInFlight: Promise<boolean> | null = null;

  constructor(config: ApiClientConfig) {
    this.baseUrl = config.baseUrl;
    this.apiPrefix = config.apiPrefix;
    this.getAccessToken = config.getAccessToken;
    this.getRefreshToken = config.getRefreshToken;
    this.onRefresh = config.onRefresh;
    this.onAuthFailure = config.onAuthFailure;
  }

  private fullUrl(path: string): string {
    return `${this.baseUrl}${this.apiPrefix}${path}`;
  }

  private buildHeaders(body: unknown, stream: boolean): Record<string, string> {
    const headers: Record<string, string> = {};
    // FormData sets its own multipart Content-Type (with a boundary) — let the
    // browser fill it in, or the server can't parse the body.
    if (body !== undefined && body !== null && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    if (stream) {
      headers["Accept"] = "text/event-stream";
    }
    const token = this.getAccessToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  private async rawFetch(
    url: string,
    method: string,
    headers: Record<string, string>,
    body: unknown,
    signal?: AbortSignal
  ): Promise<Response> {
    try {
      return await fetch(url, {
        method,
        headers,
        body:
          body instanceof FormData
            ? body
            : body !== undefined && body !== null
              ? JSON.stringify(body)
              : undefined,
        signal,
      });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") throw err;
      // fetch rejects (TypeError) only on network-level failures
      throw new ApiError(0, NETWORK_MESSAGE);
    }
  }

  /**
   * Authenticated fetch with ONE transparent token-refresh + retry on 401.
   *
   * This is the single policy for recovering an expired session — both JSON
   * request() and fetchBlob() go through it, so they can't drift apart.
   *
   * A 401 that survives (refresh failed, no refresh token, or the retried
   * request still 401s) means the session is genuinely dead. We only trigger
   * onAuthFailure() when we were actually acting as an authenticated user
   * (an auth header had been attached) — anonymous calls like login send no
   * token, and their 401 is "wrong credentials", not session expiry.
   *
   * Non-ok responses are NOT classified here; callers interpret the status.
   */
  private async authFetch(
    url: string,
    method: string,
    body: unknown,
    stream: boolean,
    signal?: AbortSignal
  ): Promise<Response> {
    const hadToken = Boolean(this.getAccessToken());
    let headers = this.buildHeaders(body, stream);

    let response = await this.rawFetch(url, method, headers, body, signal);

    if (response.status === 401 && this.getRefreshToken()) {
      const refreshed = await this.tryRefreshToken();
      if (refreshed) {
        headers = this.buildHeaders(body, stream);
        response = await this.rawFetch(url, method, headers, body, signal);
      }
    }

    if (response.status === 401 && hadToken) {
      this.onAuthFailure();
      throw new ApiError(401, "Session expired. Please log in again.");
    }

    return response;
  }

  /** Turn a non-ok response into an ApiError with the backend's detail. */
  private async parseErrorResponse(response: Response): Promise<ApiError> {
    const error = await response.json().catch(() => ({ detail: "Request failed" }));
    return new ApiError(response.status, error.detail || "Request failed");
  }

  /** Core JSON request with one transparent token-refresh + retry on 401. */
  async request<T>(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal
  ): Promise<T> {
    const response = await this.authFetch(
      this.fullUrl(path),
      method,
      body,
      false,
      signal
    );
    if (!response.ok) throw await this.parseErrorResponse(response);

    // 204 / empty body guard
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  /**
   * Fetch a binary resource (e.g. an image) with the Authorization header.
   *
   * `<img src>` can't send headers, so the backend's relative `/v1/images/file?`
   * URLs are fetched here and rendered via blob URLs. Mirrors request()'s
   * 401-refresh-retry via the shared authFetch, so expired sessions recover
   * transparently — and a session that can't be recovered signs the user out.
   */
  async fetchBlob(
    path: string,
    signal?: AbortSignal
  ): Promise<{ blob: Blob; contentType: string }> {
    const response = await this.authFetch(
      this.fullUrl(path),
      "GET",
      undefined,
      false,
      signal
    );
    if (!response.ok) throw await this.parseErrorResponse(response);

    return {
      blob: await response.blob(),
      contentType:
        response.headers.get("content-type") || "application/octet-stream",
    };
  }

  private tryRefreshToken(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;

    this.refreshInFlight = (async () => {
      const refreshToken = this.getRefreshToken();
      if (!refreshToken) return false;
      try {
        const response = await fetch(this.fullUrl("/auth/refresh"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            refresh_token: refreshToken,
            device_id: getDeviceId(),
          }),
        });
        if (!response.ok) return false;
        const data = await response.json();
        // Backend rotates the refresh token on every use — persist the new one
        // or the next refresh will fail with the now-invalidated old token.
        this.onRefresh(data.access_token, data.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        this.refreshInFlight = null;
      }
    })();

    return this.refreshInFlight;
  }

  /**
   * SSE streaming for chat.
   *
   * The backend frames each chunk as `data: <content>\n\n`, terminates with
   * `data: [DONE]\n\n`, and emits errors as `data: [ERROR] <msg>\n\n`. We parse
   * on the `\n\n` event boundary so multi-line tokens survive, and still tolerate
   * the legacy `ERROR:` / `Internal server error` shapes.
   *
   * Transport reading (the `\n\n` boundary split + tail flush) is shared with
   * `_readSseStream` via `_readSseChunks`; only the event interpreter differs
   * (plain tokens here vs JSON objects there).
   */
  async streamChat(
    body: ChatRequest,
    onToken: (token: string) => void,
    onDone: () => void,
    onError: (error: string) => void,
    signal?: AbortSignal
  ): Promise<void> {
    let response: Response;
    try {
      response = await this.authFetch(
        this.fullUrl("/v1/chat/completions"),
        "POST",
        body,
        true,
        signal
      );
      if (!response.ok) throw await this.parseErrorResponse(response);
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      if (err instanceof ApiError) {
        if (err.isRateLimit) return onError("Rate limit exceeded. Try again later.");
        return onError(err.detail);
      }
      return onError((err as Error)?.message || NETWORK_MESSAGE);
    }

    const reader = response.body?.getReader();
    if (!reader) return onError("Streaming not supported by this browser.");

    const decoder = new TextDecoder();

    try {
      for await (const ev of this._readSseChunks(reader, decoder, signal)) {
        const parsed = interpretChatFrame(ev);
        if (!parsed) continue;
        if (parsed.type === "done") {
          onDone();
          return;
        }
        if (parsed.type === "error") {
          onError(parsed.message);
          return;
        }
        onToken(parsed.token);
      }
      onDone();
    } catch (err) {
      if ((err as Error)?.name !== "AbortError") {
        onError((err as Error)?.message || NETWORK_MESSAGE);
      }
    }
  }

  /**
   * Shared SSE transport reader. Splits the byte stream on the `\n\n` event
   * boundary (so multi-line payloads survive), and flushes any trailing event
   * the stream ended without a blank line on. Both `streamChat` (plain tokens)
   * and `_readSseStream` (JSON objects) consume this; only the interpretation of
   * each raw event differs.
   */
  private async *_readSseChunks(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    decoder: TextDecoder,
    signal?: AbortSignal
  ): AsyncGenerator<string> {
    let buffer = "";
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const ev of events) {
        yield ev.replace(/\r/g, "");
      }
    }
    if (buffer.length > 0) {
      yield buffer.replace(/\r/g, "");
    }
  }

  private async *_readSseStream<T>(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    decoder: TextDecoder,
    signal?: AbortSignal
  ): AsyncGenerator<T> {
    for await (const ev of this._readSseChunks(reader, decoder, signal)) {
      const obj = parseSseJson<T>(ev);
      if (obj !== undefined) yield obj;
    }
  }

  /**
   * Generic SSE reader for endpoints that frame each `data:` line as a JSON
   * object (agent steps, research progress). Yields the parsed objects. Unlike
   * streamChat (plain `data: <token>`), this does NOT touch the chat parser, so
   * existing chat streaming is unaffected. Auth/refresh is handled by authFetch.
   */
  async *streamEvents<T = unknown>(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal
  ): AsyncGenerator<T> {
    const response = await this.authFetch(
      this.fullUrl(path),
      method,
      body,
      true,
      signal
    );
    if (!response.ok) throw await this.parseErrorResponse(response);
    const reader = response.body?.getReader();
    if (!reader) throw new ApiError(0, "Streaming not supported by this browser.");
    const decoder = new TextDecoder();
    try {
      for await (const obj of this._readSseStream<T>(reader, decoder, signal)) {
        yield obj;
      }
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      throw err;
    } finally {
      try {
        reader.releaseLock();
      } catch {
        /* already released */
      }
    }
  }
}

export const apiClient = new ApiClient({
  baseUrl: import.meta.env.VITE_API_URL || "http://localhost:2727",
  apiPrefix: import.meta.env.VITE_API_PREFIX || "",
  getAccessToken: () => useAuthStore.getState().accessToken,
  getRefreshToken: () => useAuthStore.getState().refreshToken,
  onRefresh: (access, refresh) => useAuthStore.getState().setTokens(access, refresh),
  onAuthFailure: () => useAuthStore.getState().forceLogout(),
});
