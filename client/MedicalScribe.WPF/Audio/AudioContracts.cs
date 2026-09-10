// Audio capture contracts.
// Interface-first (spec §19.4): the recorder UI binds to these abstractions;
// the NAudio/WASAPI implementation lands in Phase 2, adapted from SpeakType
// (MIT) src/SpeakType.Core/AudioRecorder.cs — which captured 16 kHz mono
// PCM16 via WaveInEvent and exposed RMS level events. MedicalScribe keeps the
// format and the level-event idea, but streams chunks to FastAPI over
// WebSocket instead of writing WAV files (STT is server-side; spec §2).
namespace MedicalScribe.WPF.Audio;

public sealed record AudioDeviceInfo(string Id, string Name, bool IsDefault, int MaxChannels);

/// <summary>PCM16 mono chunk at the negotiated sample rate — the exact unit
/// sent as audio.chunk frames (docs/WEBSOCKET_PROTOCOL.md).</summary>
public sealed record AudioChunk(byte[] Pcm16, int SampleRate, int Sequence, DateTimeOffset CapturedAt)
{
    public int DurationMs => Pcm16.Length * 1000 / (SampleRate * 2);
}

public enum CaptureState
{
    Idle,
    Starting,
    Capturing,
    Paused,
    Stopping,
    Fault,
}

public interface IAudioCaptureService : IDisposable
{
    /// <summary>Enumerate capture endpoints (WASAPI in the real impl).</summary>
    Task<IReadOnlyList<AudioDeviceInfo>> ListDevicesAsync(CancellationToken ct = default);

    CaptureState State { get; }
    TimeSpan Elapsed { get; }

    /// <summary>Normalized RMS 0..1, raised ~10×/s on the UI dispatcher.</summary>
    event EventHandler<float>? LevelChanged;

    event EventHandler<CaptureState>? StateChanged;
    event EventHandler<AudioChunk>? ChunkAvailable;
    event EventHandler<Exception>? Faulted;

    Task StartAsync(string deviceId, CancellationToken ct = default);
    Task StopAsync(CancellationToken ct = default);
    void Pause();
    void Resume();
}
