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

// Shell state: auth gate, navigation, connection banner, global hotkeys.
// All business logic lives in Services/ViewModels — MainWindow code-behind
// only hands the HWND to the hotkey service (pure plumbing).
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Hotkeys;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.ViewModels;

public sealed record NavItem(ScreenKind Kind, string Title, string Glyph);

public enum ScreenKind
{
    Dashboard,
    PatientEncounter,
    Recorder,
    LiveTranscript,
    MedicalEditor,
    Report,
    Templates,
    AiSettings,
    UserSettings,
}

public sealed partial class MainViewModel : ObservableObject, IDisposable
{
    private readonly IAuthorizationService _auth;
    private readonly IServerStatusService _status;
    private readonly IHotkeyService _hotkeys;
    private readonly AppSettings _settings;
    private readonly ILoggerService _logger;

    public MainViewModel(
        LoginViewModel login,
        DashboardViewModel dashboard,
        PatientEncounterViewModel patientEncounter,
        RecorderViewModel recorder,
        LiveTranscriptViewModel liveTranscript,
        MedicalEditorViewModel editor,
        ReportViewModel report,
        TemplatesViewModel templates,
        AiSettingsViewModel aiSettings,
        UserSettingsViewModel userSettings,
        IAuthorizationService auth,
        IServerStatusService status,
        IHotkeyService hotkeys,
        ISettingsStore settingsStore,
        ILoggerService logger)
    {
        _auth = auth;
        _status = status;
        _hotkeys = hotkeys;
        _settings = settingsStore.Current;
        _logger = logger;

        Login = login;
        Dashboard = dashboard;
        PatientEncounter = patientEncounter;
        Recorder = recorder;
        LiveTranscript = liveTranscript;
        Editor = editor;
        Report = report;
        Templates = templates;
        AiSettings = aiSettings;
        UserSettings = userSettings;

        NavItems =
        [
            new NavItem(ScreenKind.Dashboard, "Dashboard", "⌂"),
            new NavItem(ScreenKind.PatientEncounter, "Patient / Encounter", "☰"),
            new NavItem(ScreenKind.Recorder, "Recorder", "●"),
            new NavItem(ScreenKind.LiveTranscript, "Live Transcript", "≡"),
            new NavItem(ScreenKind.MedicalEditor, "Medical Editor", "✎"),
            new NavItem(ScreenKind.Report, "Report", "▤"),
            new NavItem(ScreenKind.Templates, "Templates", "▦"),
            new NavItem(ScreenKind.AiSettings, "AI Settings", "⚙"),
            new NavItem(ScreenKind.UserSettings, "User Settings", "☺"),
        ];

        _status.StateChanged += (_, _) => OnUi(() =>
        {
            StatusText = _status.StatusDetail;
            OnPropertyChanged(nameof(UiState));
        });
        _auth.AuthStateChanged += (_, _) => OnUi(ResyncAuthState);
        _status.Start();
        ResyncAuthState();
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

    // -- auth gate -----------------------------------------------------------

    [ObservableProperty]
    private bool _authenticated;

    public bool IsAuthenticated => Authenticated;

    private void ResyncAuthState()
    {
        Authenticated = _auth.IsAuthenticated;
        if (Authenticated)
        {
            NavigateTo(ScreenKind.Dashboard);
        }
        else
        {
            CurrentViewModel = Login;
            SelectedNavItem = null;
        }
    }

    // -- screens (singletons: state survives navigation) ----------------------

    public LoginViewModel Login { get; }
    public DashboardViewModel Dashboard { get; }
    public PatientEncounterViewModel PatientEncounter { get; }
    public RecorderViewModel Recorder { get; }
    public LiveTranscriptViewModel LiveTranscript { get; }
    public MedicalEditorViewModel Editor { get; }
    public ReportViewModel Report { get; }
    public TemplatesViewModel Templates { get; }
    public AiSettingsViewModel AiSettings { get; }
    public UserSettingsViewModel UserSettings { get; }

    [ObservableProperty]
    private ObservableObject? _currentViewModel;

    public IReadOnlyList<NavItem> NavItems { get; }

    [ObservableProperty]
    private NavItem? _selectedNavItem;

    partial void OnSelectedNavItemChanged(NavItem? value)
    {
        if (value is not null)
        {
            NavigateTo(value.Kind);
        }
    }

    public void NavigateTo(ScreenKind kind)
    {
        if (!IsAuthenticated && kind != ScreenKind.Dashboard)
        {
            return; // screens are behind the login gate
        }
        CurrentViewModel = kind switch
        {
            ScreenKind.Dashboard => Dashboard,
            ScreenKind.PatientEncounter => PatientEncounter,
            ScreenKind.Recorder => Recorder,
            ScreenKind.LiveTranscript => LiveTranscript,
            ScreenKind.MedicalEditor => Editor,
            ScreenKind.Report => Report,
            ScreenKind.Templates => Templates,
            ScreenKind.AiSettings => AiSettings,
            ScreenKind.UserSettings => UserSettings,
            _ => Dashboard,
        };
        var nav = NavItems.FirstOrDefault(n => n.Kind == kind);
        if (!ReferenceEquals(SelectedNavItem, nav))
        {
            SelectedNavItem = nav;
        }
    }

    [RelayCommand]
    private async Task LogoutAsync()
    {
        await _auth.LogoutAsync();
    }

    // -- connection banner ----------------------------------------------------

    [ObservableProperty]
    private string _statusText = "connecting…";

    public ConnectionState UiState => _status.State;

    // -- hotkeys ----------------------------------------------------------------
    // Called by MainWindow.OnSourceInitialized (window handle plumbing only).
    public void OnWindowHandle(IntPtr handle)
    {
        _hotkeys.SetWindowHandle(handle);
        if (!_settings.HotkeysEnabled)
        {
            return;
        }
        if (HotkeyGesture.TryParse(_settings.ToggleRecordingHotkey, out var gesture))
        {
            var ok = _hotkeys.TryRegister(in gesture, "toggle-recording", () => OnUi(() => Recorder.ToggleRecording()));
            if (!ok)
            {
                _logger.Warn($"hotkey {_settings.ToggleRecordingHotkey} already taken or invalid");
            }
        }
    }

    public void Dispose()
    {
        _status.Stop();
        _hotkeys.UnregisterAll();
        _hotkeys.Dispose();
    }
}
