// Recorder screen: mic selection, record state machine, elapsed time, audio
// level, connection/STT state, errors (spec §15). Capture itself is a stub
// until Phase 2; the state machine and UI wiring are real and tested.
using System.Collections.ObjectModel;
using System.Diagnostics;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Audio;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class RecorderViewModel : ObservableObject, IDisposable
{
    private readonly IAudioCaptureService _capture;
    private readonly IServerStatusService _status;
    private readonly Stopwatch _watch = new();
    private readonly System.Timers.Timer _tick;

    public RecorderViewModel(IAudioCaptureService capture, IServerStatusService status)
    {
        _capture = capture;
        _status = status;

        _capture.StateChanged += (_, state) => OnUi(() => OnCaptureStateChanged(state));
        _capture.LevelChanged += (_, level) => OnUi(() => AudioLevel = level);
        _capture.Faulted += (_, ex) => OnUi(() => LastError = ex.Message);
        _status.StateChanged += (_, _) => OnUi(() => OnPropertyChanged(nameof(ConnectionLabel)));

        _tick = new System.Timers.Timer(200) { AutoReset = true };
        _tick.Elapsed += (_, _) => OnUi(() => Elapsed = _watch.Elapsed);
        _tick.Start();
        _ = LoadDevicesAsync();
    }

    private static void OnUi(Action action)
    {
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is not null && !dispatcher.CheckAccess())
        {
            dispatcher.BeginInvoke(action);
        }
        else
        {
            action();
        }
    }

    // -- devices ------------------------------------------------------------

    public ObservableCollection<AudioDeviceInfo> Microphones { get; } = [];

    [ObservableProperty]
    private AudioDeviceInfo? _selectedMicrophone;

    public async Task LoadDevicesAsync()
    {
        try
        {
            var devices = await _capture.ListDevicesAsync();
            OnUi(() =>
            {
                Microphones.Clear();
                foreach (var d in devices)
                {
                    Microphones.Add(d);
                }
                SelectedMicrophone =
                    Microphones.FirstOrDefault(d => d.IsDefault) ?? Microphones.FirstOrDefault();
            });
        }
        catch (Exception)
        {
            // device enumeration failure must not crash the shell
        }
    }

    // -- state machine --------------------------------------------------------

    [ObservableProperty]
    private CaptureState _state = CaptureState.Idle;

    [ObservableProperty]
    private TimeSpan _elapsed = TimeSpan.Zero;

    [ObservableProperty]
    private double _audioLevel;

    [ObservableProperty]
    private string? _lastError;

    public bool IsRecording => State == CaptureState.Capturing;
    public bool IsPaused => State == CaptureState.Paused;
    public bool CanStart => State is CaptureState.Idle or CaptureState.Fault;
    public bool CanStop => State is CaptureState.Capturing or CaptureState.Paused or CaptureState.Starting;
    public bool CanPause => State == CaptureState.Capturing;
    public bool CanResume => State == CaptureState.Paused;

    public string StateLabel => State switch
    {
        CaptureState.Idle => "Idle",
        CaptureState.Starting => "Starting…",
        CaptureState.Capturing => "● Recording",
        CaptureState.Paused => "⏸ Paused",
        CaptureState.Stopping => "Stopping…",
        CaptureState.Fault => "Fault",
        _ => "Idle",
    };

    public string ConnectionLabel => _status.State switch
    {
        ConnectionState.Online => "server: online",
        ConnectionState.Offline => "server: OFFLINE",
        ConnectionState.Degraded => "server: degraded",
        _ => "server: unknown",
    };

    public string CapabilityBanner
    {
        get
        {
            var features = _status.Manifest?.Features;
            if (_status.State != ConnectionState.Online)
            {
                return "Connect to the MedicalScribe API to enable dictation.";
            }
            if (features is null)
            {
                return "Fetching server manifest…";
            }
            return features.LiveTranscription
                ? "Live transcription enabled by server policy."
                : "Live transcription arrives in Phase 2 — the recorder state machine is active now.";
        }
    }

    private void OnCaptureStateChanged(CaptureState state)
    {
        State = state;
        if (state == CaptureState.Capturing && !_watch.IsRunning)
        {
            _watch.Restart();
        }
        else if (state is CaptureState.Idle or CaptureState.Fault or CaptureState.Stopping)
        {
            _watch.Stop();
        }
        else if (state == CaptureState.Paused)
        {
            _watch.Stop();
        }
        else if (state == CaptureState.Capturing)
        {
            _watch.Start();
        }
        OnPropertyChanged(nameof(IsRecording));
        OnPropertyChanged(nameof(CanStart));
        OnPropertyChanged(nameof(CanStop));
        OnPropertyChanged(nameof(CanPause));
        OnPropertyChanged(nameof(CanResume));
        OnPropertyChanged(nameof(StateLabel));
        StartCommand.NotifyCanExecuteChanged();
        StopCommand.NotifyCanExecuteChanged();
        PauseCommand.NotifyCanExecuteChanged();
        ResumeCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand(CanExecute = nameof(CanStart))]
    private async Task StartAsync()
    {
        LastError = null;
        await _capture.StartAsync(SelectedMicrophone?.Id ?? "", CancellationToken.None);
    }

    [RelayCommand(CanExecute = nameof(CanStop))]
    private async Task StopAsync()
    {
        await _capture.StopAsync();
    }

    [RelayCommand(CanExecute = nameof(CanPause))]
    private void Pause() => _capture.Pause();

    [RelayCommand(CanExecute = nameof(CanResume))]
    private void Resume() => _capture.Resume();

    /// <summary>Bound to the global hotkey (MainViewModel) and F9.</summary>
    public void ToggleRecording()
    {
        if (CanStart)
        {
            _ = StartAsync();
        }
        else if (IsRecording || IsPaused)
        {
            _ = StopAsync();
        }
    }

    public void Dispose()
    {
        _tick.Dispose();
        _watch.Reset();
    }
}
