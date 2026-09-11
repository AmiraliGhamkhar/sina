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

// Dashboard: server status, capability overview, phase-honest module cards.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed record ModuleCard(string Title, string Status);

public sealed partial class DashboardViewModel : ObservableObject
{
    private readonly IApiClient _api;
    private readonly IServerStatusService _status;
    private readonly IAuthorizationService _auth;

    public DashboardViewModel(IApiClient api, IServerStatusService status, IAuthorizationService auth)
    {
        _api = api;
        _status = status;
        _auth = auth;
        RefreshModules();
        _status.StateChanged += (_, _) => OnUi(RefreshModules);
        _auth.AuthStateChanged += (_, _) => OnUi(() => OnPropertyChanged(nameof(UserLabel)));
    }

    private static void OnUi(Action action)
    {
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is not null && !dispatcher.CheckAccess())
        {
            dispatcher.BeginInvoke(action);
        }
        else
        {
            action();
        }
    }

    public ObservableCollection<ProviderInfoDto> Providers { get; } = [];

    [ObservableProperty]
    private bool _isLoading;

    [ObservableProperty]
    private string? _loadError;

    public string UserLabel =>
        _auth.CurrentUser is { } u ? $"{u.UserId} ({u.Role}){(u.IsDev ? " · dev" : "")}" : "not signed in";

    public string ServerLabel => _status.State.ToString();

    public string ManifestSummary => _status.Manifest is { } m
        ? $"API v{m.ServerVersion} · ws protocol v{m.WsProtocol} · phase {m.Phase}"
        : "manifest unavailable";

    public ObservableCollection<ModuleCard> Modules { get; } = [];

    private void RefreshModules()
    {
        var features = _status.Manifest?.Features;
        Modules.Clear();
        Modules.Add(new ModuleCard("Live dictation", features?.LiveTranscription == true ? "ready" : "Phase 2"));
        Modules.Add(new ModuleCard("Voice commands", features?.VoiceCommands == true ? "ready" : "Phase 6"));
        Modules.Add(new ModuleCard("Report generation", features?.ReportGeneration == true ? "ready" : "Phase 6"));
        Modules.Add(new ModuleCard("Local AI (llama-server)", "configured via server AI settings"));
        Modules.Add(new ModuleCard("Cloud STT/LLM", features?.CloudProvidersEnabled == true ? "enabled" : "Phase 3/4"));
        Modules.Add(new ModuleCard("Patients & encounters DB", "Phase 7"));
        Modules.Add(new ModuleCard("Audit trail", features?.AuditEnabled == true ? "server-side JSONL (DB in Phase 7)" : "disabled"));
    }

    [RelayCommand]
    private async Task RefreshAsync()
    {
        IsLoading = true;
        LoadError = null;
        try
        {
            await _status.RefreshNowAsync();
            var providers = await _api.GetProvidersAsync(probeHealth: true);
            OnUi(() =>
            {
                Providers.Clear();
                foreach (var p in providers)
                {
                    Providers.Add(p);
                }
                OnPropertyChanged(nameof(ManifestSummary));
                OnPropertyChanged(nameof(ServerLabel));
            });
        }
        catch (ApiException ex)
        {
            OnUi(() => LoadError = ex.DetailMessage);
        }
        finally
        {
            OnUi(() => IsLoading = false);
        }
    }
}
