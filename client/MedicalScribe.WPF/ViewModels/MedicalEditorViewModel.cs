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

// Medical editor: post-recording transcript cleanup + terminology
// normalization + voice-command log. Backend services implement the logic
// (Phases 3/6); the editor keeps the transcript model and the edit buffer.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;

namespace MedicalScribe.WPF.ViewModels;

public sealed record CommandLogEntry(string Time, string Command, string Detail);

public sealed partial class MedicalEditorViewModel : ObservableObject
{
    private readonly LiveTranscriptViewModel _transcript;
    private readonly IApiClient? _api;

    public MedicalEditorViewModel(LiveTranscriptViewModel transcript, IApiClient? api = null)
    {
        _transcript = transcript;
        _api = api;
    }

    [ObservableProperty]
    private string _editBuffer = "";

    [ObservableProperty]
    private string? _editorNote =
        "Terminology normalization + validation run server-side; this buffer edits the transcript before report generation.";

    public ObservableCollection<CommandLogEntry> CommandLog { get; } = [];

    public string TranscriptText => _transcript.FullText;

    [RelayCommand]
    private void LoadTranscriptIntoBuffer()
    {
        EditBuffer = _transcript.FullText;
    }

    [RelayCommand]
    private void ApplyBufferToTranscript()
    {
        if (_transcript.Segments.Count == 0)
        {
            EditorNote = "nothing to apply — transcript is empty";
            return;
        }
        // Simple policy: a single revised paragraph replaces the last segment's
        // text; granular per-segment editing arrives with the editor upgrade.
        var last = _transcript.Segments[^1];
        last.Text = EditBuffer;
        last.MarkEdited();
        EditorNote = "applied to the last segment (per-segment editing UI: Phase 6).";
    }

    [RelayCommand]
    private void InsertNewParagraph()
    {
        EditBuffer = string.IsNullOrEmpty(EditBuffer) ? "\n" : EditBuffer + "\n\n";
        CommandLog.Insert(0, new CommandLogEntry(DateTime.Now.ToString("HH:mm:ss"), "new_paragraph", "via editor button"));
    }

    /// <summary>Terminology normalization PREVIEW (Phase 6): server-side
    /// canonicalization of Persian dictation variants (ام‌آرآی → MRI …).
    /// Purely a derived view — the stored transcript is never rewritten and
    /// the substitution list is reversible.</summary>
    [RelayCommand]
    private async Task NormalizePreviewAsync()
    {
        if (_api is null)
        {
            EditorNote = "normalization preview needs the API client (not configured)";
            return;
        }
        var text = string.IsNullOrWhiteSpace(EditBuffer) ? _transcript.FullText : EditBuffer;
        if (string.IsNullOrWhiteSpace(text))
        {
            EditorNote = "nothing to normalize — buffer and transcript are empty";
            return;
        }
        try
        {
            var result = await _api.NormalizeTextAsync(text);
            if (result is null)
            {
                EditorNote = "server returned no normalization result";
                return;
            }
            EditBuffer = result.Normalized;
            var applied = string.Join(", ", result.Substitutions.Select(s => $"{s.Original} → {s.Replacement}"));
            EditorNote = result.Substitutions.Count == 0
                ? "no terminology substitutions applied (text already canonical)"
                : $"{result.Substitutions.Count} reversible substitutions: {applied}";
        }
        catch (ApiException ex)
        {
            EditorNote = $"normalization failed: {ex.DetailMessage}";
        }
    }
}
