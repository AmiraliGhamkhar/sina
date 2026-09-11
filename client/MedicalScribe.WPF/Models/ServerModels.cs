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
    [property: JsonPropertyName("args")] List<string> Args,
    [property: JsonPropertyName("mode_prefixes")] List<string>? ModePrefixes = null);

public sealed record FeatureFlagsDto(
    [property: JsonPropertyName("live_transcription")] bool LiveTranscription,
    [property: JsonPropertyName("voice_commands")] bool VoiceCommands,
    [property: JsonPropertyName("report_generation")] bool ReportGeneration,
    [property: JsonPropertyName("report_lifecycle")] bool ReportLifecycle = false,
    [property: JsonPropertyName("editing_enabled")] bool EditingEnabled = true,
    [property: JsonPropertyName("cloud_providers_enabled")] bool CloudProvidersEnabled = false,
    [property: JsonPropertyName("audit_enabled")] bool AuditEnabled = true);

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
    [property: JsonPropertyName("routing_modes")] List<string> RoutingModes,
    [property: JsonPropertyName("report_statuses")] List<string>? ReportStatuses = null,
    [property: JsonPropertyName("report_template_keys")] List<string>? ReportTemplateKeys = null);

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

// ---- Phase 6: report templates (GET /api/v1/report-templates) -------------------

public sealed record TemplateSectionDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("instruction")] string Instruction,
    [property: JsonPropertyName("required")] bool Required,
    [property: JsonPropertyName("format_style")] string FormatStyle);

public sealed record ReportTemplateDto(
    [property: JsonPropertyName("key")] string Key,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("category")] string Category,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("sections")] List<TemplateSectionDto> Sections,
    [property: JsonPropertyName("builtin")] bool Builtin,
    [property: JsonPropertyName("version")] int Version);

public sealed record TemplateListDto(
    [property: JsonPropertyName("templates")] List<ReportTemplateDto> Templates,
    [property: JsonPropertyName("total")] int Total);

// ---- Phase 6: report lifecycle -----------------------------------------------------

public sealed record ReportWarningDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("code")] string Code,
    [property: JsonPropertyName("severity")] string Severity,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("section_id")] string? SectionId,
    [property: JsonPropertyName("evidence")] string? Evidence,
    [property: JsonPropertyName("acknowledged")] bool Acknowledged,
    [property: JsonPropertyName("acknowledgment_justification")] string? AcknowledgmentJustification);

public sealed record ReportSectionDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("markdown")] string Markdown,
    [property: JsonPropertyName("missing")] bool Missing);

public sealed record ReportDto(
    [property: JsonPropertyName("report_id")] string ReportId,
    [property: JsonPropertyName("encounter_id")] string EncounterId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("template_key")] string? TemplateKey,
    [property: JsonPropertyName("template_name")] string? TemplateName,
    [property: JsonPropertyName("language")] string Language,
    [property: JsonPropertyName("provider")] string? Provider,
    [property: JsonPropertyName("sections")] List<ReportSectionDto> Sections,
    [property: JsonPropertyName("warnings")] List<ReportWarningDto> Warnings,
    [property: JsonPropertyName("critical_warnings")] int CriticalWarnings,
    [property: JsonPropertyName("blocking_warnings")] int BlockingWarnings,
    [property: JsonPropertyName("amended_from")] string? AmendedFrom = null);

public sealed record ReportDraftRequestDto(
    [property: JsonPropertyName("transcript")] string? Transcript,
    [property: JsonPropertyName("session_id")] string? SessionId,
    [property: JsonPropertyName("template_key")] string? TemplateKey,
    [property: JsonPropertyName("language")] string Language = "fa-en");

public sealed record ReportDraftResponseDto(
    [property: JsonPropertyName("report_id")] string ReportId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("warnings")] List<ReportWarningDto> Warnings,
    [property: JsonPropertyName("terminology_substitutions")] int TerminologySubstitutions,
    [property: JsonPropertyName("transcript_source")] string TranscriptSource);

// ---- Phase 6: terminology normalization (POST /api/v1/terminology/normalize) ----

public sealed record SubstitutionDto(
    [property: JsonPropertyName("original")] string Original,
    [property: JsonPropertyName("replacement")] string Replacement,
    [property: JsonPropertyName("category")] string Category);

public sealed record NormalizationResultDto(
    [property: JsonPropertyName("normalized")] string Normalized,
    [property: JsonPropertyName("substitutions")] List<SubstitutionDto> Substitutions,
    [property: JsonPropertyName("reversible")] bool Reversible);

// ---- Model hub (GET/POST/DELETE /api/v1/models) ------------------------------

public sealed record ModelFileInfoDto(
    [property: JsonPropertyName("local_name")] string LocalName,
    [property: JsonPropertyName("size_bytes")] long SizeBytes,
    [property: JsonPropertyName("received_bytes")] long ReceivedBytes);

public sealed record ModelInfoDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("role")] string Role,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("license")] string License,
    [property: JsonPropertyName("license_url")] string? LicenseUrl,
    [property: JsonPropertyName("source_repo")] string SourceRepo,
    [property: JsonPropertyName("runtime")] string Runtime,
    [property: JsonPropertyName("providers")] List<string> Providers,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("progress")] double? Progress,
    [property: JsonPropertyName("received_bytes")] long ReceivedBytes,
    [property: JsonPropertyName("total_bytes")] long TotalBytes,
    [property: JsonPropertyName("current_file")] string? CurrentFile,
    [property: JsonPropertyName("error")] string? Error,
    [property: JsonPropertyName("installed_at")] string? InstalledAt,
    [property: JsonPropertyName("auto_configured")] bool AutoConfigured,
    [property: JsonPropertyName("operator_note")] string? OperatorNote,
    [property: JsonPropertyName("files")] List<ModelFileInfoDto> Files,
    [property: JsonPropertyName("install_dir")] string InstallDir);

public sealed record ModelListDto(
    [property: JsonPropertyName("models")] List<ModelInfoDto> Models);

public sealed record ModelDownloadAcceptedDto(
    [property: JsonPropertyName("model_id")] string ModelId,
    [property: JsonPropertyName("state")] string State,
    [property: JsonPropertyName("detail")] string Detail);

public sealed record ModelDeletedDto(
    [property: JsonPropertyName("model_id")] string ModelId,
    [property: JsonPropertyName("deleted")] bool Deleted,
    [property: JsonPropertyName("detail")] string Detail);
