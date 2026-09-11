// NAudio/WASAPI microphone capture — 16 kHz mono PCM16 streamed as chunks.
// ADAPTED FROM: SpeakType (MIT) src/SpeakType.Core/AudioRecorder.cs &
// AudioDeviceHelper.cs (same WaveInEvent loop, RMS level math, device
// heuristics — "skip Steam, prefer names containing mic"). Key difference:
// nothing is written to disk; chunks are pushed to consumers and streamed to
// the backend (STT is server-side per spec §2). The Steam-specific filter was
// generalized into a "virtual audio" deny-list.
using MedicalScribe.WPF.Infrastructure;
using System.Diagnostics;
using NAudio.CoreAudioApi;
using NAudio.Wave;

namespace MedicalScribe.WPF.Audio;

public sealed class NAudioCaptureService : IAudioCaptureService
{
    public const int TargetSampleRate = 16000;
    private static readonly string[] VirtualAudioHints = ["steam", "virtual", "cable", "sdi", "snapshot"];

    private readonly ILoggerService _log;
    private WaveInEvent? _waveIn;
    private int _sequence;
    private long _bytesCaptured;
    private bool _paused;
    private readonly Stopwatch _watch = new();
    private readonly object _gate = new();

    public NAudioCaptureService(ILoggerService log) => _log = log;

    public CaptureState State { get; private set; } = CaptureState.Idle;
    public TimeSpan Elapsed => _watch.Elapsed;

    public event EventHandler<float>? LevelChanged;
    public event EventHandler<CaptureState>? StateChanged;
    public event EventHandler<AudioChunk>? ChunkAvailable;
    public event EventHandler<Exception>? Faulted;

    // -- devices ---------------------------------------------------------------

    public Task<IReadOnlyList<AudioDeviceInfo>> ListDevicesAsync(CancellationToken ct = default) =>
        Task.Run<IReadOnlyList<AudioDeviceInfo>>(() =>
        {
            string? defaultId = null;
            try
            {
                using var enumerator = new MMDeviceEnumerator();
                using var def = enumerator.GetDefaultAudioEndpoint(DataCapture, Role.Multimedia);
                defaultId = def.ID;
            }
            catch (Exception)
            {
                // no default endpoint — leave names-only listing
            }

            var list = new List<AudioDeviceInfo>();
            for (var i = 0; i < WaveInEvent.DeviceCount; i++)
            {
                if (ct.IsCancellationRequested)
                {
                    break;
                }
                var name = WaveInEvent.GetCapabilities(i).ProductName;
                var lower = name.ToLowerInvariant();
                var virtualDevice = VirtualAudioHints.Any(h => lower.Contains(h, StringComparison.Ordinal));
                list.Add(new AudioDeviceInfo(
                    Id: $"wavein-{i}",
                    Name: name + (virtualDevice ? " (virtual)" : ""),
                    IsDefault: defaultId is not null && NameMatches(name, defaultId),
                    MaxChannels: WaveInEvent.GetCapabilities(i).Channels));
            }
            return list;
        }, ct);

    private static bool NameMatches(string waveInName, string mmDeviceId) =>
        // MMDevice IDs are opaque; compare on the friendly name via registry-safe heuristic
        mmDeviceId.Contains(waveInName, StringComparison.OrdinalIgnoreCase) ||
        waveInName.Length > 3 && mmDeviceId.EndsWith(waveInName[^4..], StringComparison.OrdinalIgnoreCase);

    private static int ResolveDeviceNumber(string deviceId)
    {
        if (deviceId.StartsWith("wavein-", StringComparison.Ordinal) &&
            int.TryParse(deviceId[7..], out var idx) && idx >= 0 && idx < WaveInEvent.DeviceCount)
        {
            return idx;
        }
        // auto-pick: skip virtual devices, prefer real mics (SpeakType heuristic, generalized)
        for (var i = 0; i < WaveInEvent.DeviceCount; i++)
        {
            var name = WaveInEvent.GetCapabilities(i).ProductName.ToLowerInvariant();
            if (VirtualAudioHints.Any(h => name.Contains(h, StringComparison.Ordinal)))
            {
                continue;
            }
            if (name.Contains("microphone", StringComparison.Ordinal) || name.Contains("mic", StringComparison.Ordinal))
            {
                return i;
            }
        }
        for (var i = 0; i < WaveInEvent.DeviceCount; i++)
        {
            var name = WaveInEvent.GetCapabilities(i).ProductName.ToLowerInvariant();
            if (!VirtualAudioHints.Any(h => name.Contains(h, StringComparison.Ordinal)))
            {
                return i;
            }
        }
        return WaveInEvent.DeviceCount > 0 ? 0 : -1;
    }

    // -- control ---------------------------------------------------------------

    public Task StartAsync(string deviceId, CancellationToken ct = default)
    {
        lock (_gate)
        {
            if (State is CaptureState.Capturing or CaptureState.Starting)
            {
                return Task.CompletedTask;
            }
            var deviceNumber = ResolveDeviceNumber(deviceId);
            if (deviceNumber < 0)
            {
                RaiseFault(new InvalidOperationException("no capture device available"));
                return Task.CompletedTask;
            }
            try
            {
                SetState(CaptureState.Starting);
                _waveIn = new WaveInEvent { DeviceNumber = deviceNumber };
                _waveIn.WaveFormat = new WaveFormat(TargetSampleRate, 16, 1); // 16 kHz mono PCM16
                _waveIn.DataAvailable += OnDataAvailable;
                _waveIn.RecordingStopped += OnRecordingStopped;
                _bytesCaptured = 0;
                _sequence = 0;
                _paused = false;
                _waveIn.StartRecording();
                _watch.Restart();
                SetState(CaptureState.Capturing);
                _log.Info($"capture started on device #{deviceNumber} @ {TargetSampleRate} Hz mono");
            }
            catch (Exception ex)
            {
                CleanupWaveIn();
                RaiseFault(ex);
            }
        }
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken ct = default)
    {
        lock (_gate)
        {
            if (_waveIn is null)
            {
                SetState(CaptureState.Idle);
                return Task.CompletedTask;
            }
            SetState(CaptureState.Stopping);
            try
            {
                _waveIn.StopRecording();
            }
            catch (MmException ex)
            {
                RaiseFault(ex);
            }
        }
        return Task.CompletedTask;
    }

    public void Pause()
    {
        lock (_gate)
        {
            if (State != CaptureState.Capturing)
            {
                return;
            }
            _paused = true;
            SetState(CaptureState.Paused);
        }
    }

    public void Resume()
    {
        lock (_gate)
        {
            if (State != CaptureState.Paused)
            {
                return;
            }
            _paused = false;
            SetState(CaptureState.Capturing);
        }
    }

    // -- data path ---------------------------------------------------------------

    private void OnDataAvailable(object? sender, WaveInEventArgs e)
    {
        // level is computed even while paused so the UI meter stays alive
        if (e.BytesRecorded > 0)
        {
            var level = ComputeRms(e.Buffer, e.BytesRecorded);
            LevelChanged?.Invoke(this, level);
        }
        if (_paused || e.BytesRecorded == 0 || State != CaptureState.Capturing)
        {
            return;
        }
        var pcm = new byte[e.BytesRecorded];
        Buffer.BlockCopy(e.Buffer, 0, pcm, 0, e.BytesRecorded);
        _bytesCaptured += e.BytesRecorded;
        ChunkAvailable?.Invoke(
            this,
            new AudioChunk(pcm, TargetSampleRate, ++_sequence, DateTimeOffset.UtcNow));
    }

    /// <summary>RMS normalized to 0..1 — math adapted from SpeakType AudioRecorder.</summary>
    private static float ComputeRms(byte[] buffer, int bytes)
    {
        double sum = 0;
        var samples = bytes / 2;
        for (var i = 0; i + 1 < bytes; i += 2)
        {
            var sample = (short)((buffer[i + 1] << 8) | buffer[i]);
            var normalized = sample / 32768.0;
            sum += normalized * normalized;
        }
        if (samples == 0)
        {
            return 0f;
        }
        return (float)Math.Clamp(Math.Sqrt(sum / samples) * 4.0, 0.0, 1.0);
    }

    private void OnRecordingStopped(object? sender, StoppedEventArgs e)
    {
        lock (_gate)
        {
            CleanupWaveIn();
            _watch.Stop();
            if (e.Exception is not null)
            {
                _log.Error("capture stopped with error", e.Exception);
                SetState(CaptureState.Fault);
                Faulted?.Invoke(this, e.Exception);
            }
            else
            {
                SetState(CaptureState.Idle);
            }
        }
    }

    private void CleanupWaveIn()
    {
        if (_waveIn is null)
        {
            return;
        }
        _waveIn.DataAvailable -= OnDataAvailable;
        _waveIn.RecordingStopped -= OnRecordingStopped;
        _waveIn.Dispose();
        _waveIn = null;
    }

    private void RaiseFault(Exception ex)
    {
        SetState(CaptureState.Fault);
        Faulted?.Invoke(this, ex);
    }

    private void SetState(CaptureState state)
    {
        if (State == state)
        {
            return;
        }
        State = state;
        StateChanged?.Invoke(this, state);
    }

    public void Dispose()
    {
        try
        {
            lock (_gate)
            {
                _waveIn?.StopRecording();
                CleanupWaveIn();
            }
        }
        catch (Exception)
        {
            // dispose is best effort — never throw from teardown
        }
        _watch.Reset();
        GC.SuppressFinalize(this);
    }
}
