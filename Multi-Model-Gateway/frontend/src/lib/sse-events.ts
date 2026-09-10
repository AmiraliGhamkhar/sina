/**
 * SSE event interpretation — "what does a raw frame mean".
 *
 * The transport (api-client) splits the byte stream into raw frames on the
 * `\n\n` boundary via `_readSseChunks`. This module owns everything about
 * turning those raw frames into domain events for the app's two wire
 * protocols:
 *   - the plain-token chat protocol (`data: <token>` / `[DONE]` / `[ERROR]`)
 *   - the JSON-object protocol (`data: {json}`), used by agent + research
 *
 * Keeping interpretation here — rather than in the transport — makes each wire
 * protocol testable in isolation against a raw frame string, and keeps
 * api-client a narrow byte/HTTP adapter. The typed agent-event union
 * (agent-events.ts, ADR-0007) stays a layer above: it is what consumers (e.g.
 * use-agent) annotate the JSON payload `parseSseJson` yields as, and provides
 * the named FileEditResult parsing for tool_result content.
 */

export type ChatStreamEvent =
  | { type: "token"; token: string }
  | { type: "done" }
  | { type: "error"; message: string };

export const CHAT_DONE_MARKER = "[DONE]";

/**
 * Interpret one raw chat frame into a stream event, or `null` for frames we
 * should ignore (blank lines, malformed shapes). Mirrors the backend contract
 * in services/router.py: `data: <token>`, `data: [DONE]`, `data: [ERROR] <msg>`,
 * plus the legacy un-prefixed `ERROR: ` and `Internal server error` shapes.
 *
 * Operates on the frame exactly as the transport's frame splitter yields it
 * (`\r` already stripped, boundary `\n\n` removed) — it does NOT trim the frame,
 * so token payload whitespace survives intact.
 */
export function interpretChatFrame(frame: string): ChatStreamEvent | null {
  if (frame.length === 0) return null;

  if (frame.startsWith("data: ")) {
    const payload = frame.slice(6);
    if (payload === CHAT_DONE_MARKER) return { type: "done" };
    if (payload.startsWith("[ERROR]"))
      return {
        type: "error",
        message: payload.slice(7).trim() || "Something went wrong.",
      };
    if (payload.startsWith("ERROR: ")) return { type: "error", message: payload.slice(7) };
    return { type: "token", token: payload };
  }

  // Un-prefixed error frames from the backend
  if (frame.startsWith("ERROR: ")) return { type: "error", message: frame.slice(7) };
  if (frame.trim() === "Internal server error")
    return { type: "error", message: "Internal server error" };
  return null;
}

/**
 * Parse a raw JSON SSE frame (`data: {json}`) into the payload object, if any.
 * Returns `undefined` for frames that carry no JSON (blank lines, `[DONE]`,
 * non-data frames, or malformed JSON — which is skipped rather than thrown).
 */
export function parseSseJson<T = unknown>(frame: string): T | undefined {
  const event = frame.replace(/\r/g, "").trim();
  if (!event.startsWith("data:")) return undefined;
  const payload = event.slice(5).trim();
  if (!payload || payload === CHAT_DONE_MARKER) return undefined;
  try {
    return JSON.parse(payload) as T;
  } catch {
    return undefined;
  }
}
