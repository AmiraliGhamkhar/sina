/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Templates screen (Phase 6): fully data-driven (spec §10) — the catalog is
// served by GET /api/v1/report-templates; nothing in the UI branches on
// template identity. Built-ins are immutable server-side; clinicians fork
// them into custom templates (the fork is an explicit server action).
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed record TemplateRow(
    string Key,
    string Name,
    string Category,
    bool Builtin,
    int Version,
    string SectionsSummary);

public sealed partial class TemplatesViewModel : ObservableObject
{
    private readonly IApiClient _api;

    public TemplatesViewModel(IApiClient api)
    {
        _api = api;
    }

    public ObservableCollection<TemplateRow> Templates { get; } = [];

    [ObservableProperty]
    private TemplateRow? _selected;

    [ObservableProperty]
    private string _note = "Templates are server data — press Refresh to load the catalog.";

    [ObservableProperty]
    private string _newKey = "";

    [ObservableProperty]
    private string _newName = "";

    public bool CanForkSelected =>
        Selected is { Builtin: true } &&
        !string.IsNullOrWhiteSpace(NewKey) &&
        NewKey.Trim().Length >= 2;

    [RelayCommand]
    private async Task RefreshAsync()
    {
        try
        {
            var templates = await _api.GetReportTemplatesAsync();
            Templates.Clear();
            foreach (var t in templates)
            {
                Templates.Add(new TemplateRow(
                    t.Key, t.Name, t.Category, t.Builtin, t.Version,
                    string.Join(" · ", t.Sections.Select(s => s.Id))));
            }
            Note = templates.Count == 0
                ? "server returned an empty catalog"
                : $"{templates.Count} templates loaded ({templates.Count(t => t.Builtin)} built-in, "
                  + $"{templates.Count(t => !t.Builtin)} custom)";
        }
        catch (ApiException ex)
        {
            Note = $"server unreachable — template list is empty ({ex.DetailMessage})";
        }
    }

    [RelayCommand]
    private async Task ForkSelectedAsync()
    {
        if (!CanForkSelected || Selected is null)
        {
            Note = "Fork needs a built-in template selected plus a new key (a-z, 0-9, '-').";
            return;
        }
        try
        {
            // fork endpoint: server copies sections; the client renders the result
            var forked = await ForkOnServerAsync(Selected.Key, NewKey.Trim().ToLowerInvariant(),
                string.IsNullOrWhiteSpace(NewName) ? null : NewName.Trim());
            Note = $"Forked '{Selected.Key}' → '{forked}' as a custom template.";
            NewKey = "";
            NewName = "";
            await RefreshAsync();
        }
        catch (ApiException ex)
        {
            Note = $"Fork rejected: {ex.DetailMessage}";
        }
    }

    private Task<string> ForkOnServerAsync(string sourceKey, string newKey, string? newName) =>
        _api.ForkTemplateAsync(sourceKey, newKey, newName);

    partial void OnSelectedChanged(TemplateRow? value)
    {
        OnPropertyChanged(nameof(CanForkSelected));
        ForkSelectedCommand.NotifyCanExecuteChanged();
    }

    partial void OnNewKeyChanged(string value)
    {
        OnPropertyChanged(nameof(CanForkSelected));
        ForkSelectedCommand.NotifyCanExecuteChanged();
    }
}
