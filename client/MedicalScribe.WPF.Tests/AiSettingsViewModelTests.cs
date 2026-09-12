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

// AI Settings view-model behavior: the STT-provider picker (which providers are
// pinnable, how a saved preference is restored, and what happens when it names
// something the server no longer has) plus 9Router model-catalog discovery.
// Headless — ObservableCollection + CommunityToolkit need no WPF Application.
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Settings;
using MedicalScribe.WPF.ViewModels;
using Xunit;

namespace MedicalScribe.WPF.Tests;

/// <summary>Scripted IApiClient for the AI Settings screen.</summary>
public sealed class FakeAiSettingsApiClient : IApiClient
{
    public List<ProviderInfoDto> ProviderList { get; } = [];
    public Dictionary<string, ProviderModelCatalogDto> Catalogs { get; } = new(StringComparer.Ordinal);
    public List<(string Provider, string Kind)> ModelCalls { get; } = [];
    public Exception? ThrowOnProviders { get; set; }
    public Exception? ThrowOnModels { get; set; }

    public Task<IReadOnlyList<ProviderInfoDto>> GetProvidersAsync(bool probeHealth = false, CancellationToken ct = default)
    {
        if (ThrowOnProviders is not null)
        {
            throw ThrowOnProviders;
        }
        return Task.FromResult<IReadOnlyList<ProviderInfoDto>>(ProviderList.ToList());
    }

    public Task<ProviderModelCatalogDto?> GetProviderModelsAsync(
        string provider, string kind = "llm", CancellationToken ct = default)
    {
        ModelCalls.Add((provider, kind));
        if (ThrowOnModels is not null)
        {
            throw ThrowOnModels;
        }
        return Task.FromResult(Catalogs.TryGetValue($"{provider}:{kind}", out var c) ? c : null);
    }

    // -- unused by this screen ---------------------------------------------------

    public Uri BaseAddress => new("http://localhost:8000");
    public Task<HealthDto?> GetHealthAsync(CancellationToken ct = default) => Task.FromResult<HealthDto?>(null);
    public Task<VersionDto?> GetVersionAsync(CancellationToken ct = default) => Task.FromResult<VersionDto?>(null);
    public Task<ClientManifestDto?> GetManifestAsync(CancellationToken ct = default) => Task.FromResult<ClientManifestDto?>(null);
    public Task<TokenPairDto> LoginAsync(LoginRequestDto request, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<TokenPairDto> RefreshAsync(string refreshToken, CancellationToken ct = default) => throw new NotSupportedException();
    public Task LogoutAsync(CancellationToken ct = default) => Task.CompletedTask;
    public Task<PrincipalDto?> GetCurrentUserAsync(CancellationToken ct = default) => Task.FromResult<PrincipalDto?>(null);
    public Task<IReadOnlyList<ReportTemplateDto>> GetReportTemplatesAsync(CancellationToken ct = default) => throw new NotSupportedException();
    public Task<string> ForkTemplateAsync(string sourceKey, string newKey, string? newName, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDraftResponseDto> CreateReportDraftAsync(ReportDraftRequestDto request, string encounterId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto?> GetReportAsync(string reportId, CancellationToken ct = default) => Task.FromResult<ReportDto?>(null);
    public Task<ReportDto> PatchReportSectionsAsync(string reportId, IReadOnlyDictionary<string, string> sections, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> AcknowledgeWarningAsync(string reportId, string warningId, string justification, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> FinalizeReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> ReopenReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> ApproveReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> AmendReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<NormalizationResultDto?> NormalizeTextAsync(string text, CancellationToken ct = default) => Task.FromResult<NormalizationResultDto?>(null);
    public Task<IReadOnlyList<ModelInfoDto>> GetModelsAsync(CancellationToken ct = default) => Task.FromResult<IReadOnlyList<ModelInfoDto>>(new List<ModelInfoDto>());
    public Task<ModelInfoDto?> GetModelAsync(string modelId, CancellationToken ct = default) => Task.FromResult<ModelInfoDto?>(null);
    public Task<ModelDownloadAcceptedDto> StartModelDownloadAsync(string modelId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ModelDeletedDto> DeleteModelAsync(string modelId, CancellationToken ct = default) => throw new NotSupportedException();
}

public sealed class FakeLogger : ILoggerService
{
    public List<string> Errors { get; } = [];
    public void Info(string message) { }
    public void Warn(string message) { }
    public void Error(string message, Exception? exception = null) => Errors.Add(message);
    public string LogFilePath => "(test)";
}

public sealed class AiSettingsViewModelTests : IDisposable
{
    private readonly string _settingsPath =
        Path.Combine(Path.GetTempPath(), $"ms-ai-settings-{Guid.NewGuid():N}.json");

    public void Dispose()
    {
        try
        {
            File.Delete(_settingsPath);
        }
        catch (IOException)
        {
            // best effort temp cleanup
        }
    }

    private static ProviderInfoDto Provider(
        string name, string kind, bool configured, bool streaming = true,
        string privacy = "local", bool discovery = false, ProviderHealthDto? health = null) =>
        new(
            name,
            kind,
            $"{name} ({kind})",
            configured,
            health,
            new ProviderCapabilitiesDto(privacy, streaming, new List<string> { "fa", "en" }, 500, 0.0),
            discovery);

    private static readonly ProviderInfoDto[] TypicalProviders =
    [
        Provider("mock", "stt", configured: true),
        Provider("mock", "llm", configured: true),
        Provider("9router", "stt", configured: true, discovery: true,
            health: new ProviderHealthDto(true, 42.0, null)),
        Provider("9router", "llm", configured: true, discovery: true,
            health: new ProviderHealthDto(true, 38.0, null)),
        Provider("speechmatics", "stt", configured: false, privacy: "cloud"),
        Provider("whisper-local", "stt", configured: true, streaming: false),
    ];

    private (AiSettingsViewModel vm, FakeAiSettingsApiClient api, JsonSettingsStore store) Build(
        Action<AppSettings>? configure = null, ProviderInfoDto[]? providers = null)
    {
        var api = new FakeAiSettingsApiClient();
        api.ProviderList.AddRange(providers ?? TypicalProviders);
        var store = new JsonSettingsStore(_settingsPath);
        configure?.Invoke(store.Current);
        var vm = new AiSettingsViewModel(api, store, new FakeLogger());
        return (vm, api, store);
    }

    // -- provider picker ---------------------------------------------------------

    [Fact]
    public async Task RefreshDefaultsToServerDecides()
    {
        var (vm, _, store) = Build();
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal(AiSettingsViewModel.ServerDecides, vm.SelectedSttProvider!.Value);
        Assert.Equal("", store.Current.PreferredSttProvider);
        Assert.Null(vm.UnavailableSttProvider);
    }

    [Fact]
    public async Task OnlyConfiguredStreamingSttProvidersArePinnable()
    {
        var (vm, _, _) = Build();
        await vm.RefreshCommand.ExecuteAsync(null);

        // sentinel first, then only the configured + streaming STT providers:
        //  · speechmatics is registered but unconfigured → the server could not honour it
        //  · whisper-local is configured but batch-only → cannot serve a live session
        //  · the llm "mock" is not a dictation engine (the stt "mock" is)
        Assert.Equal(
            new[] { AiSettingsViewModel.ServerDecides, "9router", "mock" },
            vm.SttProviderChoices.Select(c => c.Value).ToArray());
    }

    [Fact]
    public async Task SavedPreferenceIsRestoredWithoutBeingRewritten()
    {
        var (vm, _, store) = Build(s => s.PreferredSttProvider = "9router");
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal("9router", vm.SelectedSttProvider!.Value);
        Assert.Null(vm.UnavailableSttProvider);
        // hydrating the picker must not dirty the stored settings
        Assert.Equal("9router", store.Current.PreferredSttProvider);
    }

    [Fact]
    public async Task UnavailableSavedPreferenceFallsBackWithAWarning()
    {
        var (vm, _, store) = Build(s => s.PreferredSttProvider = "deepgram");
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal("deepgram", vm.UnavailableSttProvider);
        Assert.Contains("deepgram", vm.UnavailableSttProviderNote!);
        Assert.Contains("not configured", vm.UnavailableSttProviderNote!);
        // dictation must not silently pin to a provider the server will reject
        Assert.Equal(AiSettingsViewModel.ServerDecides, vm.SelectedSttProvider!.Value);
        // …but the saved preference is left intact: the clinician may reconnect
        // to a server that does have deepgram, and only *this* server lacks it.
        Assert.Equal("deepgram", store.Current.PreferredSttProvider);
    }

    [Fact]
    public async Task ChoosingAProviderPersistsItImmediately()
    {
        var (vm, _, store) = Build();
        await vm.RefreshCommand.ExecuteAsync(null);

        vm.SelectedSttProvider = vm.SttProviderChoices.First(c => c.Value == "9router");

        Assert.Equal("9router", store.Current.PreferredSttProvider);
        Assert.Contains("9router", vm.PreferredProviderNote);
        Assert.True(File.Exists(_settingsPath));
        Assert.Contains("9router", File.ReadAllText(_settingsPath));
    }

    [Fact]
    public async Task ChoosingServerDecidesClearsThePreference()
    {
        var (vm, _, store) = Build(s => s.PreferredSttProvider = "9router");
        await vm.RefreshCommand.ExecuteAsync(null);

        vm.SelectedSttProvider = vm.SttProviderChoices.First(c => c.Value == AiSettingsViewModel.ServerDecides);

        Assert.Equal("", store.Current.PreferredSttProvider);
        Assert.Contains("router picks", vm.PreferredProviderNote);
    }

    [Fact]
    public async Task ProviderListingFailureSurfacesAsLoadError()
    {
        var (vm, api, _) = Build();
        api.ThrowOnProviders = ApiException.Network("cannot reach the API");

        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal("cannot reach the API", vm.LoadError);
        Assert.False(vm.IsBusy);
        // the picker still offers the sentinel so the screen stays usable
        Assert.Single(vm.SttProviderChoices);
    }

    // -- 9Router panel -----------------------------------------------------------

    [Fact]
    public async Task NineRouterPanelAppearsOnlyWhenRegistered()
    {
        var (vm, _, _) = Build();
        await vm.RefreshCommand.ExecuteAsync(null);
        Assert.True(vm.HasNineRouter);
        Assert.True(vm.CanDiscoverNineRouterModels);
        Assert.True(vm.DiscoverNineRouterModelsCommand.CanExecute(null));
        Assert.Contains("LLM configured", vm.NineRouterStatusSummary);
        Assert.Contains("STT configured", vm.NineRouterStatusSummary);
        Assert.Contains("healthy", vm.NineRouterStatusSummary);

        var (bare, _, _) = Build(providers: new[] { Provider("mock", "stt", configured: true) });
        await bare.RefreshCommand.ExecuteAsync(null);
        Assert.False(bare.HasNineRouter);
        Assert.False(bare.CanDiscoverNineRouterModels);
        Assert.Equal("not registered on this server", bare.NineRouterStatusSummary);
    }

    [Fact]
    public async Task UnconfiguredNineRouterCannotDiscoverModels()
    {
        var (vm, api, _) = Build(providers: new[]
        {
            Provider("9router", "stt", configured: false, discovery: true),
            Provider("9router", "llm", configured: false, discovery: true),
        });
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.True(vm.HasNineRouter);
        Assert.False(vm.CanDiscoverNineRouterModels);
        Assert.Contains("not configured", vm.NineRouterStatusSummary);

        // The command is gated two ways. Invoke it through ExecuteAsync — the
        // path that bypasses ICommand.Execute's CanExecute check — to prove the
        // method guards itself: probing an unconfigured router would report a
        // misleading "no upstream accounts are connected" rather than the
        // actionable "not configured" a 409 would carry.
        Assert.False(vm.DiscoverNineRouterModelsCommand.CanExecute(null));
        await vm.DiscoverNineRouterModelsCommand.ExecuteAsync(null);
        Assert.Empty(api.ModelCalls);
        Assert.Empty(vm.NineRouterLlmModels);
        Assert.Empty(vm.NineRouterSttModels);
        Assert.False(vm.IsDiscoveringModels);
        Assert.Null(vm.ModelDiscoveryError);
        Assert.Null(vm.ModelDiscoveryNote);
    }

    [Fact]
    public async Task DiscoverModelsPopulatesBothCatalogsAndEchoesConfiguredModel()
    {
        var (vm, api, _) = Build();
        api.Catalogs["9router:llm"] = new ProviderModelCatalogDto(
            "9router", "llm", "claude/claude-sonnet-4",
            new List<ProviderModelDto>
            {
                // deliberately out of order — the VM groups by upstream then id
                new("gemini/gemini-2.5-flash", "llm", "gemini"),
                new("claude/claude-sonnet-4", "llm", "claude", 200000, 64000),
            });
        api.Catalogs["9router:stt"] = new ProviderModelCatalogDto(
            "9router", "stt", "groq/whisper-large-v3-turbo",
            new List<ProviderModelDto> { new("groq/whisper-large-v3-turbo", "stt", "groq") });
        await vm.RefreshCommand.ExecuteAsync(null);

        await vm.DiscoverNineRouterModelsCommand.ExecuteAsync(null);

        // both catalogs are fetched, against the right provider name
        Assert.Equal(new[] { "llm", "stt" }, api.ModelCalls.Select(c => c.Kind).OrderBy(k => k).ToArray());
        Assert.All(api.ModelCalls, c => Assert.Equal("9router", c.Provider));
        Assert.Equal("claude/claude-sonnet-4", vm.NineRouterLlmModel);
        Assert.Equal("groq/whisper-large-v3-turbo", vm.NineRouterSttModel);
        Assert.Equal(
            new[] { "claude/claude-sonnet-4", "gemini/gemini-2.5-flash" },
            vm.NineRouterLlmModels.Select(m => m.Id).ToArray());
        Assert.Single(vm.NineRouterSttModels);
        Assert.Contains("2 LLM and 1 STT", vm.ModelDiscoveryNote!);
        Assert.Contains("MS_LLM__NINE_ROUTER__MODEL", vm.ModelDiscoveryNote!);
        Assert.Null(vm.ModelDiscoveryError);
        Assert.False(vm.IsDiscoveringModels);
    }

    [Fact]
    public async Task EmptyCatalogTellsTheOperatorToConnectAnUpstream()
    {
        var (vm, api, _) = Build();
        api.Catalogs["9router:llm"] = new ProviderModelCatalogDto(
            "9router", "llm", null, new List<ProviderModelDto>());
        api.Catalogs["9router:stt"] = new ProviderModelCatalogDto(
            "9router", "stt", null, new List<ProviderModelDto>());
        await vm.RefreshCommand.ExecuteAsync(null);

        await vm.DiscoverNineRouterModelsCommand.ExecuteAsync(null);

        Assert.Empty(vm.NineRouterLlmModels);
        Assert.Contains("no upstream accounts are connected", vm.ModelDiscoveryNote!);
    }

    [Fact]
    public async Task DiscoveryFailureSurfacesTheServerMessage()
    {
        var (vm, api, _) = Build();
        await vm.RefreshCommand.ExecuteAsync(null);
        api.ThrowOnModels = new ApiException(
            System.Net.HttpStatusCode.Conflict, "PROVIDER_UNAVAILABLE",
            "provider '9router' is not configured server-side (set MS_LLM__NINE_ROUTER__BASE_URL)");

        await vm.DiscoverNineRouterModelsCommand.ExecuteAsync(null);

        Assert.Contains("MS_LLM__NINE_ROUTER__BASE_URL", vm.ModelDiscoveryError!);
        Assert.False(vm.IsDiscoveringModels);
        Assert.Null(vm.ModelDiscoveryNote);
    }

    // -- DTO helpers used by the picker ------------------------------------------

    [Theory]
    [InlineData("claude/claude-sonnet-4", "claude", "claude")]
    [InlineData("gemini/gemini-2.5-flash", null, "gemini")]
    [InlineData("local-model", null, "other")]
    [InlineData("local-model", "mine", "mine")]
    public void ModelGroupFallsBackToTheIdPrefix(string id, string? ownedBy, string expected)
    {
        Assert.Equal(expected, new ProviderModelDto(id, "llm", ownedBy).Group);
    }

    [Fact]
    public void ModelDisplayAddsTheContextWindowWhenKnown()
    {
        Assert.Equal("claude/claude-sonnet-4", new ProviderModelDto("claude/claude-sonnet-4").Display);
        Assert.Equal("claude/claude-sonnet-4  ·  200k ctx",
            new ProviderModelDto("claude/claude-sonnet-4", "llm", "claude", 200000).Display);
        Assert.Equal("x/y", new ProviderModelDto("x/y", "llm", "x", 0).Display);
    }

    [Fact]
    public void SupportsModelDiscoveryDefaultsToFalseForOlderServers()
    {
        // a server predating the field deserializes to false, so the panel stays hidden
        var dto = new ProviderInfoDto(
            "mock", "stt", "d", true, null,
            new ProviderCapabilitiesDto("local", true, new List<string>(), 1, 0.0));
        Assert.False(dto.SupportsModelDiscovery);
    }
}
