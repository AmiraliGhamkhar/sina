/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Models screen (model hub): server-driven catalog of downloadable AI models
// with verified downloads and auto-configure (spec: download section +
// auto-configure). The client never downloads model weights itself — the
// backend streams + hash-verifies files server-side; this screen only
// triggers, observes, and explains.
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;

namespace MedicalScribe.WPF.ViewModels;

/// <summary>Observable row for one catalog model (state/progress mutate in place).</summary>
public sealed partial class ModelRowViewModel : ObservableObject
{
    [ObservableProperty] private string _state = "not_installed";
    [ObservableProperty] private double? _progress;
    [ObservableProperty] private long _receivedBytes;
    [ObservableProperty] private string? _currentFile;
    [ObservableProperty] private string? _error;

    public required string Id { get; init; }
    public required string Name { get; init; }
    public required string Role { get; init; }
    public required string Description { get; init; }
    public required string License { get; init; }
    public required string SourceRepo { get; init; }
    public required string Runtime { get; init; }
    public required bool AutoConfigured { get; init; }
    public required string OperatorNote { get; init; }
    public required long TotalBytes { get; init; }

    public string SizeText => TotalBytes >= 1_000_000_000
        ? $"{TotalBytes / 1_000_000_000.0:0.0} GB"
        : $"{TotalBytes / 1_000_000.0:0} MB";

    public string RoleText => Role switch
    {
        "stt-shenava" => "STT · Persian (in-process)",
        "stt-whisper" => "STT · Multilingual (whisper.cpp)",
        "llm-llama" => "LLM · note drafting (llama.cpp)",
        "pii-redaction" => "Privacy · PII redaction (in-process)",
        _ => Role,
    };

    public string StateText => State switch
    {
        "installed" => "Ready" + (AutoConfigured ? " · auto-configured" : ""),
        "downloading" => "Downloading…",
        "error" => "Failed",
        _ => "Not installed",
    };

    partial void OnStateChanged(string value)
    {
        OnPropertyChanged(nameof(StateText));
        OnPropertyChanged(nameof(IsDownloading));
        OnPropertyChanged(nameof(HasError));
    }

    public bool IsDownloading => State == "downloading";
    public bool HasError => State == "error";
    public double ProgressOrZero => Progress ?? 0.0;

    public bool CanDownload => State is "not_installed" or "error";
    public bool CanDelete => State is "installed" or "error";

    public void Apply(ModelInfoDto dto)
    {
        State = dto.State;
        Progress = dto.Progress;
        ReceivedBytes = dto.ReceivedBytes;
        CurrentFile = dto.CurrentFile;
        Error = dto.Error;
        OnPropertyChanged(nameof(CanDownload));
        OnPropertyChanged(nameof(CanDelete));
    }
}

public sealed partial class ModelsViewModel : ObservableObject
{
    private readonly IApiClient _api;
    private readonly SemaphoreSlim _pollGate = new(1, 1);
    private CancellationTokenSource? _pollLoop;

    public ModelsViewModel(IApiClient api)
    {
        _api = api;
    }

    public ObservableCollection<ModelRowViewModel> Models { get; } = [];

    [ObservableProperty]
    private string _note = "Models are server-managed — press Refresh to load the catalog.";

    [ObservableProperty]
    private bool _busy;

    public bool AnyDownloading => Models.Any(m => m.State == "downloading");

    [RelayCommand]
    private async Task RefreshAsync()
    {
        if (Busy)
        {
            return;
        }
        Busy = true;
        try
        {
            var models = await _api.GetModelsAsync();
            foreach (var dto in models)
            {
                var row = Models.FirstOrDefault(m => m.Id == dto.Id);
                if (row is null)
                {
                    row = new ModelRowViewModel
                    {
                        Id = dto.Id,
                        Name = dto.Name,
                        Role = dto.Role,
                        Description = dto.Description,
                        License = dto.License,
                        SourceRepo = dto.SourceRepo,
                        Runtime = dto.Runtime,
                        AutoConfigured = dto.AutoConfigured,
                        OperatorNote = dto.OperatorNote ?? "",
                        TotalBytes = dto.TotalBytes,
                    };
                    Models.Add(row);
                }
                row.Apply(dto);
            }
            // drop rows the server no longer catalogs
            var known = models.Select(m => m.Id).ToHashSet();
            foreach (var stale in Models.Where(m => !known.Contains(m.Id)).ToList())
            {
                Models.Remove(stale);
            }
            OnPropertyChanged(nameof(AnyDownloading));
            Note = AnyDownloading
                ? "downloads in progress — updating…"
                : $"{Models.Count} models · {Models.Count(m => m.State == "installed")} ready";
            EnsurePolling();
        }
        catch (ApiException ex)
        {
            Note = $"server unreachable — model list not loaded ({ex.DetailMessage})";
        }
        finally
        {
            Busy = false;
        }
    }

    [RelayCommand]
    private async Task DownloadAsync(ModelRowViewModel? row)
    {
        if (row is null || !row.CanDownload)
        {
            return;
        }
        try
        {
            var accepted = await _api.StartModelDownloadAsync(row.Id);
            row.State = accepted.State == "installed" ? "installed" : "downloading";
            row.Error = null;
            OnPropertyChanged(nameof(AnyDownloading));
            Note = accepted.State == "installed"
                ? $"{row.Name}: already installed."
                : $"{row.Name}: download started ({row.SizeText}).";
            EnsurePolling();
        }
        catch (ApiException ex)
        {
            row.Error = ex.DetailMessage;
            Note = $"{row.Name}: download not started — {ex.DetailMessage}";
        }
    }

    [RelayCommand]
    private async Task DeleteAsync(ModelRowViewModel? row)
    {
        if (row is null || !row.CanDelete)
        {
            return;
        }
        try
        {
            var result = await _api.DeleteModelAsync(row.Id);
            Note = result.Deleted
                ? $"{row.Name}: artifacts removed (restart external services that use it)."
                : $"{row.Name}: nothing to remove.";
            await RefreshAsync();
        }
        catch (ApiException ex)
        {
            Note = $"{row.Name}: delete failed — {ex.DetailMessage}";
        }
    }

    /// <summary>Poll every ~2 s while any download is active; stops itself when idle.</summary>
    private void EnsurePolling()
    {
        if (!AnyDownloading)
        {
            _pollLoop?.Cancel();
            _pollLoop = null;
            return;
        }
        if (_pollLoop is not null)
        {
            return;
        }
        _pollLoop = new CancellationTokenSource();
        _ = PollLoopAsync(_pollLoop);
    }

    private async Task PollLoopAsync(CancellationTokenSource cts)
    {
        var ct = cts.Token;
        try
        {
            while (!ct.IsCancellationRequested && AnyDownloading)
            {
                await Task.Delay(TimeSpan.FromSeconds(2), ct);
                if (!await _pollGate.WaitAsync(TimeSpan.Zero, ct))
                {
                    continue;
                }
                try
                {
                    var models = await _api.GetModelsAsync(ct);
                    foreach (var dto in models)
                    {
                        Models.FirstOrDefault(m => m.Id == dto.Id)?.Apply(dto);
                    }
                    OnPropertyChanged(nameof(AnyDownloading));
                    var failed = models.FirstOrDefault(m => m.State == "error");
                    if (failed is not null)
                    {
                        Note = $"{failed.Name}: {failed.Error ?? "download failed"}";
                    }
                    else if (!AnyDownloading)
                    {
                        Note = $"{Models.Count(m => m.State == "installed")} model(s) ready.";
                    }
                }
                finally
                {
                    _pollGate.Release();
                }
            }
        }
        catch (OperationCanceledException)
        {
            // polling stopped — fine
        }
        catch (Exception)
        {
            // transient poll failure: next user action re-triggers EnsurePolling
        }
        finally
        {
            // only clear if a newer loop hasn't taken over
            if (ReferenceEquals(_pollLoop, cts))
            {
                _pollLoop = null;
            }
        }
    }
}
