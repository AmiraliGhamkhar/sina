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

// Templates screen — data-driven like the rest of the platform (spec §10):
// the catalog is served by the backend (GET /api/v1/report-templates in
// Phase 6). Built-in keys are listed here only as an *expected* set for the
// picker before the endpoint exists; nothing in the UI branches on template
// identity, it just displays what the server returns.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed record TemplateRow(string Key, string Name, string Origin, string Status);

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
    private string _note =
        "Template CRUD + AI extraction (Phlox-style \"generate template from example note\") land in Phase 6.";

    [RelayCommand]
    private async Task RefreshAsync()
    {
        try
        {
            // GET /api/v1/report-templates — Phase 6. Until then show the
            // built-ins the server manifest implies are coming.
            var manifest = await _api.GetManifestAsync();
            Note = manifest is null
                ? "server unreachable — template list is empty"
                : $"server v{manifest.ServerVersion} · templates are server data (Phase 6)";
            Templates.Clear();
            foreach (var (key, name) in ExpectedTemplates)
            {
                Templates.Add(new TemplateRow(key, name, "built-in (planned)", "pending Phase 6"));
            }
        }
        catch (ApiException ex)
        {
            Note = $"template fetch failed: {ex.DetailMessage}";
        }
    }

    private static readonly (string Key, string Name)[] ExpectedTemplates =
    [
        ("general-clinical-note", "General clinical note"),
        ("soap", "SOAP note"),
        ("radiology", "Radiology report"),
        ("ultrasound", "Ultrasound report"),
        ("ct", "CT report"),
        ("mri", "MRI report"),
    ];
}
