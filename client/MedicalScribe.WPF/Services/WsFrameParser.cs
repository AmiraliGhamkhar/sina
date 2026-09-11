/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Net.WebSockets;

// WebSocket frame model + pure parser for server→client frames
// (docs/WEBSOCKET_PROTOCOL.md v1). Deliberately allocation-light and
// UI-free so it is unit-testable on any runner.

namespace MedicalScribe.WPF.Services;

public abstract record WsEvent(string Type)
{
    public string SessionId { get; init; } = "";
}

public sealed record WsStartedEvent(string Provider, string Mode, string Language)
    : WsEvent("session.started");

public sealed record WsInterimEvent(string Text, string SegmentId, int StartMs)
    : WsEvent("transcript.interim");

public sealed record WsFinalEvent(
    string Text, string SegmentId, int StartMs, int EndMs, string? Language, double? Confidence)
    : WsEvent("transcript.final");

/// <summary>Voice command recognized server-side (protocol v1: type
/// "command.detected"). Args is a dict (e.g. {"section_title": "طرح"}).</summary>
public sealed record WsCommandEvent(
    string Command,
    IReadOnlyDictionary<string, string> Args,
    string UtteranceText,
    string? SegmentId)
    : WsEvent("command.detected");

public sealed record WsWarningEvent(string Code, string Message, string? SegmentId)
    : WsEvent("warning");

/// <summary>Server error frame. Recoverable=false ⇒ connection will close
/// (4400/4408/4409/4503 close codes); true ⇒ transient, keep streaming.</summary>
public sealed record WsErrorEvent(string Code, string Message, bool Recoverable, string? Field)
    : WsEvent("error");

public sealed record WsAckEvent(string AckFor, string? State) : WsEvent("ack");

public sealed record WsCompletedEvent(int SegmentCount, long DurationMs, string? Provider)
    : WsEvent("session.completed");

public sealed record WsHeartbeatEvent(long ServerTimeMs) : WsEvent("heartbeat.ping");

public sealed record WsUnknownEvent(string UnknownType) : WsEvent("unknown");

public static class WsFrameParser
{
    /// <summary>Parse one server frame. Returns null for malformed JSON —
    /// a broken frame must never kill the receive loop. Unknown frame types
    /// map to <see cref="WsUnknownEvent"/> (forward-compat, spec §"v1 is
    /// additive: ignore unknown fields").</summary>
    public static WsEvent? Parse(string json)
    {
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(json);
        }
        catch (JsonException)
        {
            return null;
        }
        using (doc)
        {
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                return null;
            }
            var type = Str(root, "type") ?? "";
            var sessionId = Str(root, "session_id") ?? "";

            WsEvent evt = type switch
            {
                "session.started" => new WsStartedEvent(
                    Str(root, "provider") ?? "", Str(root, "mode") ?? "", Str(root, "language") ?? "")
                { SessionId = sessionId },
                "transcript.interim" => new WsInterimEvent(
                    Str(root, "text") ?? "", Str(root, "segment_id") ?? "", Int(root, "start_ms"))
                { SessionId = sessionId },
                "transcript.final" => new WsFinalEvent(
                    Str(root, "text") ?? "", Str(root, "segment_id") ?? "", Int(root, "start_ms"),
                    Int(root, "end_ms"), Str(root, "language"), Dbl(root, "confidence"))
                { SessionId = sessionId },
                "command.detected" => new WsCommandEvent(
                    Str(root, "command") ?? "", StrDict(root, "args"),
                    Str(root, "utterance_text") ?? "", Str(root, "segment_id"))
                { SessionId = sessionId },
                "warning" => new WsWarningEvent(
                    Str(root, "code") ?? "", Str(root, "message") ?? "", Str(root, "segment_id"))
                { SessionId = sessionId },
                "error" => new WsErrorEvent(
                    Str(root, "code") ?? "UNKNOWN", Str(root, "message") ?? "",
                    Bool(root, "recoverable"), Str(root, "field"))
                { SessionId = sessionId },
                "ack" => new WsAckEvent(Str(root, "ack_for") ?? "", Str(root, "state"))
                { SessionId = sessionId },
                "heartbeat.ping" => new WsHeartbeatEvent(Long(root, "server_time_ms"))
                { SessionId = sessionId },
                "session.completed" => new WsCompletedEvent(
                    Int(root, "segment_count"), Long(root, "duration_ms"), Str(root, "provider"))
                { SessionId = sessionId },
                _ => new WsUnknownEvent(type) { SessionId = sessionId },
            };
            return evt;
        }
    }

    private static string? Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static int Int(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.TryGetInt32(out var i) ? i : 0;

    private static long Long(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.TryGetInt64(out var l) ? l : 0;

    private static double? Dbl(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;

    private static bool Bool(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.True;

    private static IReadOnlyDictionary<string, string> StrDict(JsonElement e, string name)
    {
        if (!e.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Object)
        {
            return new Dictionary<string, string>();
        }
        var dict = new Dictionary<string, string>();
        foreach (var prop in v.EnumerateObject())
        {
            if (prop.Value.ValueKind == JsonValueKind.String)
            {
                dict[prop.Name] = prop.Value.GetString() ?? "";
            }
        }
        return dict;
    }

    private static IReadOnlyList<string> Arr(JsonElement e, string name)
    {
        if (!e.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array)
        {
            return Array.Empty<string>();
        }
        var list = new List<string>();
        foreach (var item in v.EnumerateArray())
        {
            if (item.ValueKind == JsonValueKind.String)
            {
                list.Add(item.GetString()!);
            }
            else
            {
                list.Add(item.GetRawText()); // keep non-string args readable
            }
        }
        return list;
    }
}
