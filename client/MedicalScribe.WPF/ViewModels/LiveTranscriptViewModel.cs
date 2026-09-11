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
using System.Net.WebSockets;

// Live transcript screen: interim + final segments, timestamps, editing,
// clinician warnings. The WebSocket feed (Phase 2) calls AppendFinal /
// SetInterim; the editing behaviors are implemented now because they are
// pure view-model logic (and unit-testable without audio).
using System.Collections.ObjectModel;
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

    /// <summary>Server session id of the current/last dictation (set when
    /// session.started arrives; consumed by report drafting).</summary>
    [ObservableProperty]
    private string? _sessionId;

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

    public void SetSessionId(string? sessionId) => SessionId = sessionId;

    /// <summary>Structural marker from a voice command — rendered as its own
    /// entry so the paragraph/section structure survives editing.</summary>
    public void InsertMarker(TranscriptSegmentEntry marker)
    {
        Segments.Add(marker);
        SetInterim(null);
        OnPropertyChanged(nameof(FullText));
        UpdateSegmentCommands();
    }

    /// <summary>Mirror of the server delete-last-sentence effect: remove the
    /// last sentence of the last text-bearing entry (markers skipped).</summary>
    public void RemoveLastSentence()
    {
        for (var i = Segments.Count - 1; i >= 0; i--)
        {
            var entry = Segments[i];
            if (string.IsNullOrWhiteSpace(entry.Text) || entry.Origin == SegmentOrigin.Command)
            {
                continue;
            }
            var sentences = SplitSentences(entry.Text);
            if (sentences.Count <= 1)
            {
                Segments.RemoveAt(i);
            }
            else
            {
                entry.Text = string.Join(" ", sentences.Take(sentences.Count - 1));
                entry.MarkEdited();
            }
            OnPropertyChanged(nameof(FullText));
            UpdateSegmentCommands();
            return;
        }
    }

    private static readonly System.Text.RegularExpressions.Regex SentenceSplit =
        new(@"(?<=[.!?؟؛])\s+", System.Text.RegularExpressions.RegexOptions.Compiled);

    /// <summary>Split on sentence-final punctuation, KEEPING the punctuation
    /// attached — mirrors the server's delete-last-sentence semantics.</summary>
    private static List<string> SplitSentences(string text) =>
        SentenceSplit.Split(text)
            .Select(s => s.Trim())
            .Where(s => s.Length > 0)
            .ToList();

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
