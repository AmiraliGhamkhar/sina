// Live transcript screen: interim + final segments, timestamps, editing,
// clinician warnings. The WebSocket feed (Phase 2) calls AppendFinal /
// SetInterim; the editing behaviors are implemented now because they are
// pure view-model logic (and unit-testable without audio).
using System.Collections.ObjectModel;
using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class LiveTranscriptViewModel : ObservableObject, ILiveTranscriptSink
{
    [ObservableProperty]
    private string _interimText = "";

    [ObservableProperty]
    private bool _isListening;

    [ObservableProperty]
    private TranscriptSegmentEntry? _selectedSegment;

    [ObservableProperty]
    private string _statusNote = "idle — start a dictation from the recorder screen";

    public ObservableCollection<TranscriptSegmentEntry> Segments { get; } = [];

    public ObservableCollection<TranscriptWarning> Warnings { get; } = [];

    public string FullText
    {
        get
        {
            var sb = new StringBuilder();
            foreach (var s in Segments)
            {
                sb.AppendLine(s.Text);
                sb.AppendLine();
            }
            return sb.ToString().TrimEnd();
        }
    }

    // -- stream ingestion API (called by the WS client in Phase 2) ------------

    public void SetInterim(string? text)
    {
        InterimText = text ?? string.Empty;
        IsListening = !string.IsNullOrEmpty(text);
    }

    public void AppendFinal(TranscriptSegmentEntry segment)
    {
        Segments.Add(segment);
        SetInterim(null);
        OnPropertyChanged(nameof(FullText));
        UpdateSegmentCommands();
    }

    public void AddWarning(TranscriptWarning warning) => Warnings.Add(warning);

    public void SetStatusNote(string note) => StatusNote = note;

    public void ClearTranscript()
    {
        Segments.Clear();
        Warnings.Clear();
        SetInterim(null);
        OnPropertyChanged(nameof(FullText));
        UpdateSegmentCommands();
    }

    // -- editing (voice-command targets reuse these) --------------------------

    private bool CanDeleteLast() => Segments.Count > 0;

    [RelayCommand(CanExecute = nameof(CanDeleteLast))]
    private void DeleteLastSentence()
    {
        if (Segments.Count == 0)
        {
            return;
        }
        Segments.RemoveAt(Segments.Count - 1);
        OnPropertyChanged(nameof(FullText));
        UpdateSegmentCommands();
    }

    private bool CanEditSelected() => SelectedSegment is not null;

    partial void OnSelectedSegmentChanged(TranscriptSegmentEntry? value) =>
        CommitSelectedTextEditCommand.NotifyCanExecuteChanged();

    [RelayCommand(CanExecute = nameof(CanEditSelected))]
    private void CommitSelectedTextEdit()
    {
        if (SelectedSegment is null)
        {
            return;
        }
        SelectedSegment.MarkEdited();
        OnPropertyChanged(nameof(FullText));
    }

    /// <summary>new-paragraph voice command effect (server marks the boundary;
    /// client-side no-op fallback keeps the text sane).</summary>
    public void BreakParagraph()
    {
        OnPropertyChanged(nameof(FullText));
    }

    private void UpdateSegmentCommands()
    {
        DeleteLastSentenceCommand.NotifyCanExecuteChanged();
    }
}
