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

// The single payload the client SENDS as JSON over the WebSocket. Field names
// are snake_case per docs/WEBSOCKET_PROTOCOL.md §session.start; the strict
// server rejects unknown fields, so this record is intentionally minimal.
using System.Text.Json.Serialization;

namespace MedicalScribe.WPF.Models;

public sealed record WsStartPayload(
    [property: JsonPropertyName("v")] int V,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("language")] string Language,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("provider")] string? Provider = null,
    [property: JsonPropertyName("privacy_required")] bool? PrivacyRequired = null);
