import { describe, it, expect } from "vitest";
import { parseAgentEvent } from "./agent-events";

describe("parseAgentEvent", () => {
  it("parses a done event with a conversation_id string", () => {
    const ev = parseAgentEvent({ type: "done", conversation_id: "c9" });
    expect(ev).toEqual({ type: "done", conversation_id: "c9" });
  });

  it("parses a done event with conversation_id null (regression: agent early-failure)", () => {
    // The backend emits {"type":"done","conversation_id":null} when the agent
    // 404s/403s before the runtime starts. The parser used to drop this frame
    // (typeof string guard), so consumers never saw stream completion.
    const ev = parseAgentEvent({ type: "done", conversation_id: null });
    expect(ev).toEqual({ type: "done", conversation_id: null });
  });

  it("keeps carrying optional agent metadata on done", () => {
    const ev = parseAgentEvent({ type: "done", conversation_id: "c1", truncated: true, agent_id: "a1", agent_version: 3 });
    expect(ev).toEqual({ type: "done", conversation_id: "c1", truncated: true, agent_id: "a1", agent_version: 3 });
  });

  it("still accepts the other event shapes", () => {
    expect(parseAgentEvent({ type: "tool_call", id: "1", name: "x", arguments: "{}" })?.type).toBe("tool_call");
    expect(parseAgentEvent({ type: "tool_result", id: "1", name: "x", content: "ok" })?.type).toBe("tool_result");
    expect(parseAgentEvent({ type: "token", content: "hi" })?.type).toBe("token");
    expect(parseAgentEvent({ type: "error", message: "boom" })?.type).toBe("error");
  });

  it("rejects malformed shapes", () => {
    expect(parseAgentEvent(null)).toBeNull();
    expect(parseAgentEvent({ type: "done" })).toBeNull();
    expect(parseAgentEvent({ type: "done", conversation_id: 123 })).toBeNull();
    expect(parseAgentEvent({ type: "nope" })).toBeNull();
  });
});
