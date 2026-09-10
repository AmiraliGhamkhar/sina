// Server DTOs — mirrors backend/api/schemas (wire contract v1).
// The client mirrors field shapes only; all policy values (audio format,
// voice commands, feature flags) come from the server manifest at runtime.
using System.Text.Json.Serialization;

namespace MedicalScribe.WPF.Models;

public sealed record HealthDto(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("version")] string Version,
    [property: JsonPropertyName("phase")] int Phase);

public sealed record VersionDto(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("api_version")] string ApiVersion,
    [property: JsonPropertyName("ws_protocol")] int WsProtocol,
    [property: JsonPropertyName("ws_protocol_min")] int WsProtocolMin,
    [property: JsonPropertyName("phase")] int Phase);

public sealed record LanguageOptionDto(
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("preserves_embedded_english_terms")] bool PreservesEmbeddedEnglishTerms);

public sealed record VoiceCommandDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("triggers")] List<string> Triggers,
    [property: JsonPropertyName("args")] List<string> Args);

public sealed record FeatureFlagsDto(
    [property: JsonPropertyName("live_transcription")] bool LiveTranscription,
    [property: JsonPropertyName("voice_commands")] bool VoiceCommands,
    [property: JsonPropertyName("report_generation")] bool ReportGeneration,
    [property: JsonPropertyName("editing_enabled")] bool EditingEnabled,
    [property: JsonPropertyName("cloud_providers_enabled")] bool CloudProvidersEnabled,
    [property: JsonPropertyName("audit_enabled")] bool AuditEnabled);

public sealed record ClientManifestDto(
    [property: JsonPropertyName("server_version")] string ServerVersion,
    [property: JsonPropertyName("phase")] int Phase,
    [property: JsonPropertyName("ws_protocol")] int WsProtocol,
    [property: JsonPropertyName("ws_path")] string WsPath,
    [property: JsonPropertyName("max_message_bytes")] int MaxMessageBytes,
    [property: JsonPropertyName("audio_format")] Dictionary<string, JsonElement> AudioFormat,
    [property: JsonPropertyName("languages")] List<LanguageOptionDto> Languages,
    [property: JsonPropertyName("features")] FeatureFlagsDto Features,
    [property: JsonPropertyName("voice_commands")] List<VoiceCommandDto> VoiceCommands,
    [property: JsonPropertyName("routing_modes")] List<string> RoutingModes);

public sealed record ProviderCapabilitiesDto(
    [property: JsonPropertyName("privacy_class")] string PrivacyClass,
    [property: JsonPropertyName("supports_streaming")] bool SupportsStreaming,
    [property: JsonPropertyName("languages")] List<string> Languages,
    [property: JsonPropertyName("latency_hint_ms")] int LatencyHintMs,
    [property: JsonPropertyName("cost_hint_per_unit")] double CostHintPerUnit);

public sealed record ProviderHealthDto(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("latency_ms")] double? LatencyMs,
    [property: JsonPropertyName("detail")] string? Detail);

public sealed record ProviderInfoDto(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("configured")] bool Configured,
    [property: JsonPropertyName("health")] ProviderHealthDto? Health,
    [property: JsonPropertyName("capabilities")] ProviderCapabilitiesDto Capabilities);

public sealed record LoginRequestDto(
    [property: JsonPropertyName("username")] string Username,
    [property: JsonPropertyName("password")] string Password,
    [property: JsonPropertyName("device_name")] string? DeviceName = null);

public sealed record TokenPairDto(
    [property: JsonPropertyName("access_token")] string AccessToken,
    [property: JsonPropertyName("refresh_token")] string RefreshToken,
    [property: JsonPropertyName("token_type")] string TokenType,
    [property: JsonPropertyName("expires_in")] int ExpiresIn);

public sealed record PrincipalDto(
    [property: JsonPropertyName("user_id")] string UserId,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("is_dev")] bool IsDev);
