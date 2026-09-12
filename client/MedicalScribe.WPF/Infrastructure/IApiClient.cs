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

// Typed REST client — the ONLY way the WPF app talks to the platform.
// Hard boundary (spec §2): no direct provider calls from the client, ever.
using MedicalScribe.WPF.Models;

namespace MedicalScribe.WPF.Infrastructure;

public interface IApiClient
{
    /// <summary>Server base URL in use (for the settings screen).</summary>
    Uri BaseAddress { get; }

    Task<HealthDto?> GetHealthAsync(CancellationToken ct = default);
    Task<VersionDto?> GetVersionAsync(CancellationToken ct = default);
    Task<ClientManifestDto?> GetManifestAsync(CancellationToken ct = default);
    Task<IReadOnlyList<ProviderInfoDto>> GetProvidersAsync(bool probeHealth = false, CancellationToken ct = default);

    /// <summary>Live model catalog for a provider that declares
    /// <see cref="ProviderInfoDto.SupportsModelDiscovery"/> (9Router).
    /// <paramref name="kind"/> is "llm" or "stt". A provider that is unknown,
    /// has no catalog, or is not configured server-side surfaces as an
    /// <see cref="ApiException"/> carrying the server's own message — callers
    /// display <see cref="ApiException.DetailMessage"/>.</summary>
    Task<ProviderModelCatalogDto?> GetProviderModelsAsync(
        string provider, string kind = "llm", CancellationToken ct = default);

    Task<TokenPairDto> LoginAsync(LoginRequestDto request, CancellationToken ct = default);

    /// <summary>Phase 8: rotate the token pair (one-time refresh token);
    /// stores the new pair in the token store on success.</summary>
    Task<TokenPairDto> RefreshAsync(string refreshToken, CancellationToken ct = default);
    Task LogoutAsync(CancellationToken ct = default);
    Task<PrincipalDto?> GetCurrentUserAsync(CancellationToken ct = default);

    // -- Phase 6: templates + report lifecycle ---------------------------------

    /// <summary>Server-side template catalog (data-driven UI, spec §10).</summary>
    Task<IReadOnlyList<ReportTemplateDto>> GetReportTemplatesAsync(CancellationToken ct = default);

    /// <summary>Fork a (built-in) template into an editable custom copy.</summary>
    Task<string> ForkTemplateAsync(string sourceKey, string newKey, string? newName, CancellationToken ct = default);

    /// <summary>Grounded draft generation (transcript or session-sourced).</summary>
    Task<ReportDraftResponseDto> CreateReportDraftAsync(
        ReportDraftRequestDto request, string encounterId, CancellationToken ct = default);

    Task<ReportDto?> GetReportAsync(string reportId, CancellationToken ct = default);

    /// <summary>Clinician section edit (draft/finalized only — approved is immutable).</summary>
    Task<ReportDto> PatchReportSectionsAsync(
        string reportId, IReadOnlyDictionary<string, string> sections, CancellationToken ct = default);

    /// <summary>Acknowledge a validation warning with a recorded justification.</summary>
    Task<ReportDto> AcknowledgeWarningAsync(
        string reportId, string warningId, string justification, CancellationToken ct = default);

    Task<ReportDto> FinalizeReportAsync(string reportId, CancellationToken ct = default);
    Task<ReportDto> ReopenReportAsync(string reportId, CancellationToken ct = default);
    Task<ReportDto> ApproveReportAsync(string reportId, CancellationToken ct = default);
    Task<ReportDto> AmendReportAsync(string reportId, CancellationToken ct = default);

    /// <summary>Terminology normalization preview (reversible; the stored
    /// transcript is never rewritten server-side).</summary>
    Task<NormalizationResultDto?> NormalizeTextAsync(string text, CancellationToken ct = default);

    // -- Model hub (verified downloads + auto-configure) -------------------------

    /// <summary>Server-driven model catalog with live install status.</summary>
    Task<IReadOnlyList<ModelInfoDto>> GetModelsAsync(CancellationToken ct = default);

    /// <summary>One model's status (progress polling).</summary>
    Task<ModelInfoDto?> GetModelAsync(string modelId, CancellationToken ct = default);

    /// <summary>Start a verified background download (admin role).</summary>
    Task<ModelDownloadAcceptedDto> StartModelDownloadAsync(string modelId, CancellationToken ct = default);

    /// <summary>Remove downloaded artifacts (cancels an in-flight download).</summary>
    Task<ModelDeletedDto> DeleteModelAsync(string modelId, CancellationToken ct = default);
}
