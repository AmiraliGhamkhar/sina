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

// Transcript-side models. Wire payloads arrive via the WebSocket protocol
// (docs/WEBSOCKET_PROTOCOL.md, client lands Phase 2); these shapes are shared
// by the live transcript view and the medical editor.
using CommunityToolkit.Mvvm.ComponentModel;

namespace MedicalScribe.WPF.Models;

public enum SegmentOrigin
{
    /// <summary>Produced by STT and accepted as final.</summary>
    Dictated,

    /// <summary>Manually typed/edited by the clinician.</summary>
    Edited,

    /// <summary>Inserted by a voice command (e.g. section marker).</summary>
    Command,
}

/// <summary>
/// A finalized transcript segment. Mutability via ObservableObject because
/// the clinician can edit any segment text; the backend version tracks
/// revisions for audit (Phase 7).
/// </summary>
public sealed class TranscriptSegmentEntry : ObservableObject
{
    public TranscriptSegmentEntry(
        string segmentId, string text, int startMs, int endMs, string? language = null,
        SegmentOrigin origin = SegmentOrigin.Dictated)
    {
        SegmentId = segmentId;
        _text = text;
        StartMs = startMs;
        EndMs = endMs;
        Language = language;
        Origin = origin;
    }

    public string SegmentId { get; }
    public int StartMs { get; }
    public int EndMs { get; }
    public string? Language { get; }
    public SegmentOrigin Origin { get; }

    private string _text;

    public string Text
    {
        get => _text;
        set => SetProperty(ref _text, value);
    }

    /// <summary>True when the clinician changed provider output; drives the
    /// "modified" badge in the editor and the audit trail later.</summary>
    public bool IsModified { get; private set; }

    internal void MarkEdited() => IsModified = true;

    public string Timestamp =>
        $"{TimeSpan.FromMilliseconds(StartMs):mm\\:ss} – {TimeSpan.FromMilliseconds(EndMs):mm\\:ss}";
}

/// <summary>Warning frame emitted by the backend validation layer (spec §9):
/// dosage/unit/laterality/negation checks produce clinician-review
/// warnings, never silent rewrites.</summary>
public sealed record TranscriptWarning(
    string Code,
    string Message,
    string? SegmentId = null);
