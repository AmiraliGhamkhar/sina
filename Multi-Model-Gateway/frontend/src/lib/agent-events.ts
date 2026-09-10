/** AgentEvents - the typed SSE contract for POST /v1/agent/chat (#4).

One discriminated union. One parser from raw SSE `data:` payload to that union.
The transport (api-client) and the generic SSE codec (sse-events.ts's
parseSseJson) stay protocol-agnostic; this module is where
File Edit's {edit_id, path} shape gets named and extracted — so ToolStepCard
and use-agent stop re-parsing JSON and stop falling back to an "ok " prefix.

Deletion test: deleting this module scatters FileEditResult parsing across
use-agent + ToolStepCard + HistoryTimeline (today it already lives in two places).
*/

export type FileEditResult = {
  edit_id: string;
  path: string;
  patch?: string;
  commit_sha?: string | null;
};

export type AgentEvent =
  | { type: "tool_call"; id: string; name: string; arguments: string }
  | { type: "tool_result"; id: string; name: string; content: string }
  | { type: "token"; content: string }
  | { type: "error"; message: string }
  | { type: "done"; conversation_id: string | null; truncated?: boolean; agent_id?: string; agent_version?: number };

const FILE_EDIT_TOOLS = new Set(["edit_patch", "edit_lines", "write_file"]);

export function isFileEditTool(name: string): boolean {
  return FILE_EDIT_TOOLS.has(name);
}

/** Parse a File Edit tool_result content (JSON string) → FileEditResult | null. */
export function parseFileEditResult(content: string): FileEditResult | null {
  try {
    const obj = JSON.parse(content) as Record<string, unknown>;
    if (!obj || typeof obj !== "object") return null;
    const edit_id = obj.edit_id;
    const path = obj.path;
    if (typeof edit_id !== "string" || !edit_id) return null;
    if (typeof path !== "string" || !path) return null;
    const out: FileEditResult = { edit_id, path };
    if (typeof obj.patch === "string") out.patch = obj.patch;
    if (typeof obj.commit_sha === "string" || obj.commit_sha === null) out.commit_sha = obj.commit_sha as string | null;
    return out;
  } catch {
    return null;
  }
}

/** True when this tool_result content is a structured File Edit success. */
export function isFileEditResult(content: string): boolean {
  return parseFileEditResult(content) !== null;
}

/** Extract edit_id from a File Edit tool_result, or undefined if not one. */
export function extractEditId(content: string): string | undefined {
  return parseFileEditResult(content)?.edit_id;
}

/** Whether a tool_result for `name` with `content` should render as DiffView. */
export function shouldRenderDiff(name: string, content: string): boolean {
  if (!isFileEditTool(name)) return false;
  return isFileEditResult(content);
}

export function parseAgentEvent(raw: unknown): AgentEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const t = r.type;
  if (t === "tool_call" && typeof r.id === "string" && typeof r.name === "string") return r as AgentEvent;
  if (t === "tool_result" && typeof r.id === "string" && typeof r.name === "string" && typeof r.content === "string") return r as AgentEvent;
  if (t === "token" && typeof r.content === "string") return r as AgentEvent;
  if (t === "error" && typeof r.message === "string") return r as AgentEvent;
  if (t === "done" && (typeof r.conversation_id === "string" || r.conversation_id === null)) return r as AgentEvent;
  return null;
}
