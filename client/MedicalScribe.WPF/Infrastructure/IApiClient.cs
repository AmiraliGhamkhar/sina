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
    Task<TokenPairDto> LoginAsync(LoginRequestDto request, CancellationToken ct = default);
    Task LogoutAsync(CancellationToken ct = default);
    Task<PrincipalDto?> GetCurrentUserAsync(CancellationToken ct = default);
}
