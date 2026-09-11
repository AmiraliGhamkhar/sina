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

// Background connectivity monitor: polls /health while the shell is alive so
// every screen shows the same truthful connection state (spec §15 recorder
// "connection/STT state" requirement). Also caches the manifest — the single
// source of client policy (feature flags, voice commands, WS path).
using CommunityToolkit.Mvvm.ComponentModel;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;

namespace MedicalScribe.WPF.Services;

public enum ConnectionState
{
    Unknown,
    Online,
    Offline,
    Degraded,
}

public interface IServerStatusService
{
    ConnectionState State { get; }
    string StatusDetail { get; }
    ClientManifestDto? Manifest { get; }
    VersionDto? Version { get; }
    event EventHandler? StateChanged;
    Task RefreshNowAsync(CancellationToken ct = default);
    void Start();
    void Stop();
}

public sealed partial class ServerStatusService : ObservableObject, IServerStatusService, IDisposable
{
    private readonly IApiClient _api;
    private readonly IUiDispatcher _dispatcher;
    private readonly ILoggerService _logger;
    private CancellationTokenSource? _loop;

    public ServerStatusService(IApiClient api, IUiDispatcher dispatcher, ILoggerService logger)
    {
        _api = api;
        _dispatcher = dispatcher;
        _logger = logger;
    }

    [ObservableProperty]
    private ConnectionState _state = ConnectionState.Unknown;

    [ObservableProperty]
    private string _statusDetail = "not connected";

    [ObservableProperty]
    private ClientManifestDto? _manifest;

    [ObservableProperty]
    private VersionDto? _version;

    public event EventHandler? StateChanged;

    public void Start()
    {
        Stop();
        _loop = new CancellationTokenSource();
        _ = RunLoopAsync(_loop.Token);
    }

    public void Stop()
    {
        _loop?.Cancel();
        _loop?.Dispose();
        _loop = null;
    }

    private async Task RunLoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(10));
        await RefreshSafelyAsync(ct).ConfigureAwait(false);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                await timer.WaitForNextTickAsync(ct).ConfigureAwait(false);
                await RefreshSafelyAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                return;
            }
        }
    }

    public Task RefreshNowAsync(CancellationToken ct = default) => RefreshSafelyAsync(ct);

    private async Task RefreshSafelyAsync(CancellationToken ct)
    {
        ConnectionState newState;
        string detail;
        try
        {
            var health = await _api.GetHealthAsync(ct).ConfigureAwait(false);
            if (health is null)
            {
                newState = ConnectionState.Degraded;
                detail = "server reachable but /health returned no body";
            }
            else
            {
                newState = ConnectionState.Online;
                detail = $"v{health.Version} · phase {health.Phase}";
                var version = await _api.GetVersionAsync(ct).ConfigureAwait(false);
                var manifest = await _api.GetManifestAsync(ct).ConfigureAwait(false);
                _dispatcher.Post(() =>
                {
                    Version = version;
                    Manifest = manifest;
                });
            }
        }
        catch (OperationCanceledException)
        {
            return;
        }
        catch (ApiException ex) when (ex.IsNetworkError)
        {
            newState = ConnectionState.Offline;
            detail = ex.DetailMessage;
        }
        catch (ApiException ex)
        {
            newState = ConnectionState.Degraded;
            detail = $"{ex.Code}: {ex.DetailMessage}";
        }
        catch (Exception ex)
        {
            _logger.Error("status poll crashed", ex);
            newState = ConnectionState.Offline;
            detail = "unexpected error during health poll";
        }

        _dispatcher.Post(() =>
        {
            if (State != newState || StatusDetail != detail)
            {
                State = newState;
                StatusDetail = detail;
                _logger.Info($"connection state → {newState} ({detail})");
            }
            StateChanged?.Invoke(this, EventArgs.Empty);
        });
    }

    public void Dispose() => Stop();
}
