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

// DictationSession — the Phase 2 coordinator between microphone, stream and
// view-models (spec §19.2 layering: audio service and transport never talk to
// the UI directly; this class is the only place that knows all three).
// Flow: NAudioCaptureService.ChunkAvailable → ITranscriptionStream.SendAudio
// → backend hub → transcript.* frames → WsEvent → ILiveTranscriptSink on the
// UI dispatcher. Pause stops BOTH sides (capture discard + backend buffer).
using MedicalScribe.WPF.Audio;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.Services;

/// <summary>What the live transcript view must expose for dictation.
/// Phase 6 adds voice-command effects (markers, delete-last-sentence,
/// session identity) — the server remains the source of truth; these keep
/// the local view in sync until the next GET /transcripts/{id} refresh.</summary>
public interface ILiveTranscriptSink
{
    void SetInterim(string? text);
    void AppendFinal(TranscriptSegmentEntry segment);
    void AddWarning(TranscriptWarning warning);
    void SetStatusNote(string note);
    /// <summary>Structural marker from a voice command (paragraph/section).</summary>
    void InsertMarker(TranscriptSegmentEntry marker);
    /// <summary>Mirror of the server delete-last-sentence effect.</summary>
    void RemoveLastSentence();
    /// <summary>Current dictation session id (for report drafting).</summary>
    void SetSessionId(string? sessionId);
}

public sealed class DictationSession : IDisposable
{
    private readonly IAudioCaptureService _capture;
    private readonly ITranscriptionStream _stream;
    private readonly ILiveTranscriptSink _sink;
    private readonly IServerStatusService _status;
    private readonly ISettingsStore _settingsStore;
    private readonly ILoggerService _log;
    private readonly IUiDispatcher _ui;

    private int _finalCount;

    public DictationSession(
        IAudioCaptureService capture,
        ITranscriptionStream stream,
        ILiveTranscriptSink sink,
        IServerStatusService status,
        ISettingsStore settingsStore,
        ILoggerService log,
        IUiDispatcher ui)
    {
        _capture = capture;
        _stream = stream;
        _sink = sink;
        _status = status;
        _settingsStore = settingsStore;
        _log = log;
        _ui = ui;
        _capture.ChunkAvailable += OnChunk;
        _capture.Faulted += OnCaptureFaulted;
        _stream.StateChanged += OnStreamState;
        _stream.EventReceived += OnStreamEvent;
    }

    public bool IsStreaming { get; private set; }
    public StreamState StreamState => _stream.State;
    public int FinalSegmentCount => _finalCount;
    public string? SessionId => _stream.SessionId;

    /// <summary>Start the transcription stream first (so no audio is lost to
    /// a not-yet-open session), then the microphone. Returns false when the
    /// server does not enable live transcription — capture still refuses to
    /// start so the UI never shows "recording" with nowhere to send.</summary>
    public async Task<bool> StartAsync(string deviceId, CancellationToken ct = default)
    {
        var settings = _settingsStore.Current;
        var features = _status.Manifest?.Features;
        if (_status.Manifest is not null && features is not null && !features.LiveTranscription)
        {
            _ui.Post(() => _sink.SetStatusNote("server has live transcription disabled"));
            return false;
        }
        _finalCount = 0;
        _ui.Post(() => _sink.SetStatusNote("connecting transcription stream…"));
        var started = await _stream.StartSessionAsync(
            settings.PreferredLanguage, settings.RoutingMode,
            provider: null, privacyRequired: settings.PrivacyRequired, ct).ConfigureAwait(false);
        if (!started)
        {
            _ui.Post(() => _sink.SetStatusNote("could not start the transcription stream"));
            return false;
        }
        IsStreaming = true;
        await _capture.StartAsync(deviceId, ct).ConfigureAwait(false);
        return true;
    }

    public async Task StopAsync()
    {
        // capture first: silence the faucet, then flush the pipe
        await _capture.StopAsync().ConfigureAwait(false);
        var flushed = await _stream.StopSessionAsync(TimeSpan.FromSeconds(15)).ConfigureAwait(false);
        IsStreaming = false;
        _ui.Post(() => _sink.SetStatusNote(
            flushed
                ? $"session {SessionId} completed — {_finalCount} segments"
                : $"session {SessionId} ended without server confirmation"));
    }

    public async Task PauseAsync()
    {
        _capture.Pause();
        await _stream.PauseAsync().ConfigureAwait(false);
    }

    public async Task ResumeAsync()
    {
        await _stream.ResumeAsync().ConfigureAwait(false);
        _capture.Resume();
    }

    // -- audio path ---------------------------------------------------------------

    private void OnChunk(object? sender, AudioChunk chunk)
    {
        if (!IsStreaming)
        {
            return;
        }
        _ = _stream.SendAudioAsync(chunk.Pcm16); // never blocks capture
    }

    private void OnCaptureFaulted(object? sender, Exception ex) =>
        _ui.Post(() => _sink.SetStatusNote($"microphone error: {ex.Message}"));

    // -- stream path ---------------------------------------------------------------

    private void OnStreamState(object? sender, StreamState state) => _ui.Post(() =>
    {
        IsStreaming = state is StreamState.Connected or StreamState.Reconnecting;
        var note = state switch
        {
            StreamState.Connecting => "connecting…",
            StreamState.Connected => "stream connected",
            StreamState.Reconnecting => "reconnecting…",
            StreamState.Faulted => "stream FAULTED",
            _ => "stream closed",
        };
        _sink.SetStatusNote(note);
    });

    private void OnStreamEvent(object? sender, WsEvent evt)
    {
        if (evt is WsFinalEvent)
        {
            _finalCount++;
        }
        _ui.Post(() => ProjectEvent(evt, _sink));
    }

    /// <summary>Apply a recognized voice command to the local view. The
    /// server already applied the authoritative effect to its transcript
    /// store; this mirrors it so the UI does not diverge mid-session.</summary>
    public static void ProjectCommand(WsCommandEvent command, ILiveTranscriptSink sink)
    {
        switch (command.Command)
        {
            case "new_paragraph":
                sink.InsertMarker(new TranscriptSegmentEntry(
                    $"cmd-{command.SegmentId ?? "para"}", "— پاراگراف جدید —", 0, 0, null,
                    SegmentOrigin.Command));
                break;
            case "insert_section" when command.Args.TryGetValue("section_title", out var title):
                sink.InsertMarker(new TranscriptSegmentEntry(
                    $"cmd-{command.SegmentId ?? "sect"}", $"## {title}", 0, 0, null,
                    SegmentOrigin.Command));
                break;
            case "delete_last_sentence":
                sink.RemoveLastSentence();
                break;
            case "repeat_last":
                // the server re-appended the last dictated text; the next
                // final frame will carry it — surface the feedback only
                sink.SetStatusNote("voice command: repeat_last");
                break;
            case "pause_recording":
                sink.SetStatusNote("capture paused by voice — say 'ادامه ضبط' to resume");
                break;
            case "resume_recording":
                sink.SetStatusNote("capture resumed");
                break;
            default:
                sink.SetStatusNote($"voice command: {command.Command}");
                break;
        }
    }

    /// <summary>Pure mapping from protocol event to sink calls — public for
    /// deterministic unit tests without sockets or WPF.</summary>
    public static void ProjectEvent(WsEvent evt, ILiveTranscriptSink sink)
    {
        switch (evt)
        {
            case WsStartedEvent started:
                sink.SetSessionId(string.IsNullOrEmpty(started.SessionId) ? null : started.SessionId);
                break;
            case WsInterimEvent interim:
                sink.SetInterim(interim.Text);
                break;
            case WsFinalEvent final:
                sink.AppendFinal(new TranscriptSegmentEntry(
                    final.SegmentId, final.Text, final.StartMs, final.EndMs, final.Language));
                break;
            case WsWarningEvent warning:
                sink.AddWarning(new TranscriptWarning(warning.Code, warning.Message, warning.SegmentId));
                break;
            case WsCommandEvent command:
                ProjectCommand(command, sink);
                break;
            case WsErrorEvent error:
                sink.SetStatusNote($"server error [{error.Code}]: {error.Message}");
                break;
            case WsCompletedEvent completed:
                sink.SetStatusNote(
                    $"session complete — {completed.SegmentCount} segments in " +
                    $"{TimeSpan.FromMilliseconds(completed.DurationMs):mm\\:ss}");
                sink.SetInterim(null);
                break;
        }
    }

    public void Dispose()
    {
        _capture.ChunkAvailable -= OnChunk;
        _capture.Faulted -= OnCaptureFaulted;
        _stream.StateChanged -= OnStreamState;
        _stream.EventReceived -= OnStreamEvent;
        GC.SuppressFinalize(this);
    }
}
