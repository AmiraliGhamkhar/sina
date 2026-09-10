import { describe, it, expect } from "vitest";
import {
  interpretChatFrame,
  parseSseJson,
  CHAT_DONE_MARKER,
} from "./sse-events";

/**
 * Unit tests for the SSE interpretation codec (sse-events.ts). The transport
 * (api-client) is only a byte/HTTP adapter — it splits frames on `\n\n` and
 * hands raw strings to these pure functions, so each wire protocol is fully
 * testable here against a raw frame, with no network or DOM.
 */

describe("interpretChatFrame (the plain-token chat protocol)", () => {
  it("turns a data: payload into a token", () => {
    expect(interpretChatFrame("data: Hello")).toEqual({ type: "token", token: "Hello" });
  });

  it("preserves a token that contains a newline", () => {
    expect(interpretChatFrame("data: line1\nline2")).toEqual({
      type: "token",
      token: "line1\nline2",
    });
  });

  it("maps [DONE] to the done event", () => {
    expect(interpretChatFrame(`data: ${CHAT_DONE_MARKER}`)).toEqual({ type: "done" });
  });

  it("maps [ERROR] to an error event with the message trimmed", () => {
    expect(interpretChatFrame("data: [ERROR] something broke")).toEqual({
      type: "error",
      message: "something broke",
    });
  });

  it("defaults a bare [ERROR] to a generic message", () => {
    expect(interpretChatFrame("data: [ERROR]   ")).toEqual({
      type: "error",
      message: "Something went wrong.",
    });
  });

  it("handles the legacy `data: ERROR:` shape", () => {
    expect(interpretChatFrame("data: ERROR: legacy")).toEqual({
      type: "error",
      message: "legacy",
    });
  });

  it("handles the un-prefixed legacy error shapes", () => {
    expect(interpretChatFrame("ERROR: bare")).toEqual({ type: "error", message: "bare" });
    expect(interpretChatFrame("Internal server error")).toEqual({
      type: "error",
      message: "Internal server error",
    });
  });

  it("returns null for blank / non-event frames", () => {
    expect(interpretChatFrame("")).toBeNull();
    // A data: frame with an empty payload is the raw token "" (no whitespace
    // trimming of the frame itself, per the transport's boundary split).
    expect(interpretChatFrame("data: ")).toEqual({ type: "token", token: "" });
    expect(interpretChatFrame("data:  ")).toEqual({ type: "token", token: " " });
  });
});

describe("parseSseJson (the JSON-object protocol)", () => {
  it("parses a data: {json} frame", () => {
    expect(parseSseJson<{ type: string }>('data: {"type":"token","content":"hi"}')).toEqual({
      type: "token",
      content: "hi",
    });
  });

  it("returns undefined for [DONE] frames", () => {
    expect(parseSseJson<unknown>(`data: ${CHAT_DONE_MARKER}`)).toBeUndefined();
  });

  it("returns undefined for blank / non-data frames", () => {
    expect(parseSseJson<unknown>("")).toBeUndefined();
    expect(parseSseJson<unknown>("   ")).toBeUndefined();
    expect(parseSseJson<unknown>("some unexpected text")).toBeUndefined();
  });

  it("returns undefined for malformed JSON instead of throwing", () => {
    expect(parseSseJson<unknown>("data: {not json")).toBeUndefined();
  });

  it("tolerates a leading space after the data: colon", () => {
    expect(parseSseJson<{ a: number }>("data:  {a: 1}")).toBeUndefined(); // invalid JSON
    expect(parseSseJson<{ a: number }>('data: {"a":1}')).toEqual({ a: 1 });
  });
});
