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

// AI Settings screen — READ-ONLY view of server-side provider state plus a
// routing-mode selector. The client can never see or edit provider secrets;
// the mode is a *request*, the server's privacy policy still overrides it
// (spec §11). Provider health probing calls the backend, which probes for us.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class AiSettingsViewModel : ObservableObject
{
    private readonly IApiClient _api;
    private readonly ISettingsStore _settingsStore;
    private readonly ILoggerService _logger;

    public AiSettingsViewModel(IApiClient api, ISettingsStore settingsStore, ILoggerService logger)
    {
        _api = api;
        _settingsStore = settingsStore;
        _logger = logger;
        RoutingMode = _settingsStore.Current.RoutingMode;
    }

    public ObservableCollection<ProviderInfoDto> Providers { get; } = [];

    [ObservableProperty]
    private string? _loadError;

    [ObservableProperty]
    private bool _isBusy;

    public string[] RoutingModes { get; } = ["local", "cloud", "hybrid", "auto"];

    [ObservableProperty]
    private string _routingMode;

    public string PrivacyNote =>
        RoutingMode.Equals("cloud", StringComparison.OrdinalIgnoreCase)
            ? "⚠ Cloud mode selected: encounters marked private are STILL routed locally — privacy overrides convenience."
            : "Private encounters always use local providers, regardless of this setting.";

    partial void OnRoutingModeChanged(string value)
    {
        _settingsStore.Current.RoutingMode = value;
        _settingsStore.Save();
        OnPropertyChanged(nameof(PrivacyNote));
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

    public string DescribeHealth(ProviderInfoDto p) => p.Health switch
    {
        null => p.Configured ? "not probed" : "not configured",
        { Ok: true } h => $"healthy · {h.LatencyMs:0} ms",
        { Ok: false } h => $"DOWN · {h.Detail ?? "unreachable"}",
    };
}
