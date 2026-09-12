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

// AI Settings screen — READ-ONLY view of server-side provider state plus two
// request-level choices: the routing mode and an optional pinned STT provider.
// The client can never see or edit provider secrets; both choices are
// *requests*, and the server's privacy policy still overrides them (spec §11).
// Provider health probing calls the backend, which probes for us.
//
// The 9Router panel adds live model-catalog discovery: 9Router's "provider/model"
// ids depend on which upstream accounts the operator connected, so the list is
// fetched from GET /api/v1/providers/9router/models rather than hardcoded. It is
// informational — the model actually used is server configuration.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.ViewModels;

/// <summary>One entry of the "dictation STT provider" picker.</summary>
/// <param name="Value">Registry name sent as session.start.provider; empty
/// means "let the server's router decide".</param>
public sealed record ProviderChoice(string Value, string Display)
{
    public override string ToString() => Display;
}

public sealed partial class AiSettingsViewModel : ObservableObject
{
    /// <summary>Sentinel value meaning "no explicit provider — router decides".</summary>
    public const string ServerDecides = "";

    /// <summary>Registry name of the self-hosted multi-provider proxy.</summary>
    public const string NineRouterProviderName = "9router";

    private readonly IApiClient _api;
    private readonly ISettingsStore _settingsStore;
    private readonly ILoggerService _logger;

    /// <summary>Guards the initial picker selection so hydrating the UI from
    /// saved settings never writes back (and never clears a stale-but-valid
    /// preference before the provider list has loaded).</summary>
    private bool _suppressPersist;

    public AiSettingsViewModel(IApiClient api, ISettingsStore settingsStore, ILoggerService logger)
    {
        _api = api;
        _settingsStore = settingsStore;
        _logger = logger;
        RoutingMode = _settingsStore.Current.RoutingMode;
        // Seed the sentinel before the first refresh so the picker is usable
        // even when the server is unreachable — "let the router decide" is
        // always a valid choice.
        RebuildSttChoices();
    }

    public ObservableCollection<ProviderInfoDto> Providers { get; } = [];

    /// <summary>STT providers the clinician may pin for dictation.</summary>
    public ObservableCollection<ProviderChoice> SttProviderChoices { get; } = [];

    /// <summary>9Router LLM catalog (populated by DiscoverNineRouterModels).</summary>
    public ObservableCollection<ProviderModelDto> NineRouterLlmModels { get; } = [];

    /// <summary>9Router STT catalog (populated by DiscoverNineRouterModels).</summary>
    public ObservableCollection<ProviderModelDto> NineRouterSttModels { get; } = [];

    [ObservableProperty]
    private string? _loadError;

    [ObservableProperty]
    private bool _isBusy;

    [ObservableProperty]
    private ProviderChoice? _selectedSttProvider;

    /// <summary>Set when the saved preference names a provider the server no
    /// longer has configured — the picker falls back to "server decides".</summary>
    [ObservableProperty]
    private string? _unavailableSttProvider;

    [ObservableProperty]
    private ProviderInfoDto? _nineRouterLlm;

    [ObservableProperty]
    private ProviderInfoDto? _nineRouterStt;

    [ObservableProperty]
    private string? _nineRouterLlmModel;

    [ObservableProperty]
    private string? _nineRouterSttModel;

    [ObservableProperty]
    private bool _isDiscoveringModels;

    [ObservableProperty]
    private string? _modelDiscoveryError;

    [ObservableProperty]
    private string? _modelDiscoveryNote;

    public string[] RoutingModes { get; } = ["local", "cloud", "hybrid", "auto"];

    [ObservableProperty]
    private string _routingMode;

    public bool HasNineRouter => NineRouterLlm is not null || NineRouterStt is not null;

    /// <summary>Whether the catalog button can do anything: 9Router has to be
    /// registered AND configured server-side.</summary>
    public bool CanDiscoverNineRouterModels =>
        (NineRouterLlm?.Configured ?? false) || (NineRouterStt?.Configured ?? false);

    public string NineRouterStatusSummary
    {
        get
        {
            var parts = new List<string>(2);
            if (NineRouterLlm is { } llm)
            {
                parts.Add($"LLM {(llm.Configured ? "configured" : "not configured")} · {DescribeHealth(llm)}");
            }
            if (NineRouterStt is { } stt)
            {
                parts.Add($"STT {(stt.Configured ? "configured" : "not configured")} · {DescribeHealth(stt)}");
            }
            return parts.Count == 0 ? "not registered on this server" : string.Join("   |   ", parts);
        }
    }

    public string PrivacyNote =>
        RoutingMode.Equals("cloud", StringComparison.OrdinalIgnoreCase)
            ? "⚠ Cloud mode selected: encounters marked private are STILL routed locally — privacy overrides convenience."
            : "Private encounters always use local providers, regardless of this setting.";

    public string PreferredProviderNote =>
        SelectedSttProvider is null || SelectedSttProvider.Value.Length == 0
            ? "The server's router picks the best healthy provider per encounter (recommended)."
            : $"Dictation will request '{SelectedSttProvider.Value}'. The server may still substitute a local provider for private encounters.";

    partial void OnRoutingModeChanged(string value)
    {
        _settingsStore.Current.RoutingMode = value;
        _settingsStore.Save();
        OnPropertyChanged(nameof(PrivacyNote));
    }

    partial void OnSelectedSttProviderChanged(ProviderChoice? value)
    {
        OnPropertyChanged(nameof(PreferredProviderNote));
        if (_suppressPersist || value is null)
        {
            return;
        }
        _settingsStore.Current.PreferredSttProvider = value.Value;
        _settingsStore.Save();
    }

    partial void OnNineRouterLlmChanged(ProviderInfoDto? value) => RaiseNineRouterChanged();

    partial void OnNineRouterSttChanged(ProviderInfoDto? value) => RaiseNineRouterChanged();

    partial void OnUnavailableSttProviderChanged(string? value) =>
        OnPropertyChanged(nameof(UnavailableSttProviderNote));

    /// <summary>Warning shown when the saved preference names a provider this
    /// server no longer has configured. Null (→ collapsed) when all is well.
    /// Computed here rather than with a XAML StringFormat so the message stays
    /// testable and out of the markup.</summary>
    public string? UnavailableSttProviderNote =>
        UnavailableSttProvider is { Length: > 0 } name
            ? $"⚠ Saved provider '{name}' is not configured on this server — "
              + "falling back to letting the router decide."
            : null;

    private void RaiseNineRouterChanged()
    {
        OnPropertyChanged(nameof(HasNineRouter));
        OnPropertyChanged(nameof(CanDiscoverNineRouterModels));
        OnPropertyChanged(nameof(NineRouterStatusSummary));
        // keep the command's CanExecute in step with the refreshed provider list
        DiscoverNineRouterModelsCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand]
    private async Task RefreshAsync()
    {
        IsBusy = true;
        LoadError = null;
        try
        {
            var providers = await _api.GetProvidersAsync(probeHealth: true);
            Providers.Clear();
            foreach (var p in providers)
            {
                Providers.Add(p);
            }
            NineRouterLlm = Find(NineRouterProviderName, "llm");
            NineRouterStt = Find(NineRouterProviderName, "stt");
            RebuildSttChoices();
        }
        catch (ApiException ex)
        {
            LoadError = ex.DetailMessage;
            _logger.Error("provider listing failed", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }

    /// <summary>Fetch both 9Router catalogs. Advisory: a failure explains itself
    /// in <see cref="ModelDiscoveryError"/> and never blocks the screen.</summary>
    /// <remarks>Gated on <see cref="CanDiscoverNineRouterModels"/> *inside* the
    /// method as well as via <c>CanExecute</c>: CommunityToolkit's
    /// <c>AsyncRelayCommand.ExecuteAsync</c> does not re-check <c>CanExecute</c>
    /// (only the <c>ICommand.Execute</c> entry point does), so a programmatic
    /// invocation would otherwise probe an unconfigured router and report the
    /// misleading "no upstream accounts are connected" instead of "not
    /// configured". The button is disabled either way; this keeps the two paths
    /// honest.</remarks>
    [RelayCommand(CanExecute = nameof(CanDiscoverNineRouterModels))]
    private async Task DiscoverNineRouterModelsAsync()
    {
        if (!CanDiscoverNineRouterModels)
        {
            return;
        }

        IsDiscoveringModels = true;
        ModelDiscoveryError = null;
        ModelDiscoveryNote = null;
        try
        {
            var llmTask = (NineRouterLlm?.Configured ?? false)
                ? _api.GetProviderModelsAsync(NineRouterProviderName, "llm")
                : Task.FromResult<ProviderModelCatalogDto?>(null);
            var sttTask = (NineRouterStt?.Configured ?? false)
                ? _api.GetProviderModelsAsync(NineRouterProviderName, "stt")
                : Task.FromResult<ProviderModelCatalogDto?>(null);
            await Task.WhenAll(llmTask, sttTask);

            var llm = llmTask.Result;
            var stt = sttTask.Result;
            Replace(NineRouterLlmModels, llm?.Models);
            Replace(NineRouterSttModels, stt?.Models);
            NineRouterLlmModel = llm?.ConfiguredModel;
            NineRouterSttModel = stt?.ConfiguredModel;

            var total = NineRouterLlmModels.Count + NineRouterSttModels.Count;
            ModelDiscoveryNote = total == 0
                ? "9Router answered, but no upstream accounts are connected — add one in its dashboard."
                : $"{NineRouterLlmModels.Count} LLM and {NineRouterSttModels.Count} STT model(s) available. "
                  + "Copy an id into MS_LLM__NINE_ROUTER__MODEL / MS_STT__NINE_ROUTER__MODEL on the server.";
        }
        catch (ApiException ex)
        {
            // 409 not-configured, 501 no catalog, 502 unreachable — all arrive
            // with a message the operator can act on
            ModelDiscoveryError = ex.DetailMessage;
            _logger.Error("9router model discovery failed", ex);
        }
        finally
        {
            IsDiscoveringModels = false;
        }
    }

    private static void Replace(ObservableCollection<ProviderModelDto> target, List<ProviderModelDto>? source)
    {
        target.Clear();
        if (source is null)
        {
            return;
        }
        foreach (var model in source.OrderBy(m => m.Group, StringComparer.OrdinalIgnoreCase)
                                    .ThenBy(m => m.Id, StringComparer.OrdinalIgnoreCase))
        {
            target.Add(model);
        }
    }

    private ProviderInfoDto? Find(string name, string kind) =>
        Providers.FirstOrDefault(p =>
            string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(p.Kind, kind, StringComparison.OrdinalIgnoreCase));

    /// <summary>Only configured, streaming-capable STT providers are pinnable —
    /// anything else would be rejected or ignored server-side.</summary>
    private static bool IsSelectableStt(ProviderInfoDto p) =>
        p.Configured &&
        string.Equals(p.Kind, "stt", StringComparison.OrdinalIgnoreCase) &&
        p.Capabilities.SupportsStreaming;

    private void RebuildSttChoices()
    {
        var persisted = _settingsStore.Current.PreferredSttProvider?.Trim() ?? ServerDecides;
        SttProviderChoices.Clear();
        SttProviderChoices.Add(new ProviderChoice(ServerDecides, "server decides (recommended)"));
        foreach (var p in Providers.Where(IsSelectableStt)
                                   .OrderBy(p => p.Name, StringComparer.OrdinalIgnoreCase))
        {
            SttProviderChoices.Add(new ProviderChoice(p.Name, $"{p.Name}  ·  {p.Capabilities.PrivacyClass}"));
        }

        var match = SttProviderChoices.FirstOrDefault(c => c.Value == persisted);
        UnavailableSttProvider = match is null && persisted.Length > 0 ? persisted : null;
        _suppressPersist = true;
        try
        {
            SelectedSttProvider = match ?? SttProviderChoices[0];
        }
        finally
        {
            _suppressPersist = false;
        }
    }

    public string DescribeHealth(ProviderInfoDto p) => p.Health switch
    {
        null => p.Configured ? "not probed" : "not configured",
        { Ok: true } h => h.Detail is { Length: > 0 } note
            ? $"healthy · {h.LatencyMs:0} ms · {note}"
            : $"healthy · {h.LatencyMs:0} ms",
        { Ok: false } h => $"DOWN · {h.Detail ?? "unreachable"}",
    };
}
