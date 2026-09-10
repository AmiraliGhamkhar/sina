// Phase 1 implementation: contracts wired, real capture in Phase 2.
// Registered so the UI, DI graph and state machine work today; the recorder
// view surfaces this honestly instead of pretending audio flows.
namespace MedicalScribe.WPF.Audio;

public sealed class UnavailableAudioCaptureService : IAudioCaptureService
{
    public const string PhaseMessage =
        "audio capture lands in Phase 2 (NAudio/WASAPI); recorder UI is live now";

    public CaptureState State { get; private set; } = CaptureState.Idle;

    public TimeSpan Elapsed => TimeSpan.Zero;

    public event EventHandler<float>? LevelChanged;
    public event EventHandler<CaptureState>? StateChanged;
    public event EventHandler<AudioChunk>? ChunkAvailable;
    public event EventHandler<Exception>? Faulted;

    public Task<IReadOnlyList<AudioDeviceInfo>> ListDevicesAsync(CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<AudioDeviceInfo>>(Array.Empty<AudioDeviceInfo>());

    public Task StartAsync(string deviceId, CancellationToken ct = default) =>
        Fault(PhaseMessage);

    public Task StopAsync(CancellationToken ct = default)
    {
        SetState(CaptureState.Idle);
        return Task.CompletedTask;
    }

    public void Pause() => SetState(CaptureState.Paused);

    public void Resume() => SetState(CaptureState.Idle);

    public void Dispose() => SetState(CaptureState.Idle);

    private Task Fault(string message)
    {
        SetState(CaptureState.Fault);
        Faulted?.Invoke(this, new InvalidOperationException(message));
        return Task.CompletedTask;
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
}
