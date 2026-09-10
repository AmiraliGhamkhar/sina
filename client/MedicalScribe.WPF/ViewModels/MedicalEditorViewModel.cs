// Medical editor: post-recording transcript cleanup + terminology
// normalization + voice-command log. Backend services implement the logic
// (Phases 3/6); the editor keeps the transcript model and the edit buffer.
using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace MedicalScribe.WPF.ViewModels;

public sealed record CommandLogEntry(string Time, string Command, string Detail);

public sealed partial class MedicalEditorViewModel : ObservableObject
{
    private readonly LiveTranscriptViewModel _transcript;

    public MedicalEditorViewModel(LiveTranscriptViewModel transcript)
    {
        _transcript = transcript;
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
}
