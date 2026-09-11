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

// User settings: server URL, language, hotkey, theme. Persisted to the local
// JSON store (pattern adapted from SpeakType, see Settings/JsonSettingsStore).
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class UserSettingsViewModel : ObservableObject
{
    private readonly ISettingsStore _store;
    private readonly IApiClient _api;

    public UserSettingsViewModel(ISettingsStore store, IApiClient api)
    {
        _store = store;
        _api = api;
        var s = store.Current;
        _serverUrl = s.ServerUrl;
        _preferredLanguage = s.PreferredLanguage;
        _hotkey = s.ToggleRecordingHotkey;
        _hotkeysEnabled = s.HotkeysEnabled;
        _theme = s.Theme;
        _autoStart = s.AutoStartTranscription;
        _verboseLogging = s.VerboseLogging;
    }

    [ObservableProperty]
    private string _serverUrl;

    [ObservableProperty]
    private string _preferredLanguage;

    [ObservableProperty]
    private string _hotkey;

    [ObservableProperty]
    private bool _hotkeysEnabled;

    [ObservableProperty]
    private string _theme;

    [ObservableProperty]
    private bool _autoStart;

    [ObservableProperty]
    private bool _verboseLogging;

    [ObservableProperty]
    private string? _message;

    public string[] Languages { get; } = ["fa", "en", "fa-en"];
    public string[] Themes { get; } = ["light", "dark", "system"];

    public string StoragePathHint => _store.FilePath;

    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        try
        {
            var health = await _api.GetHealthAsync();
            Message = health is null
                ? "server replied but no health body"
                : $"connected · API v{health.Version} · phase {health.Phase}";
        }
        catch (ApiException ex)
        {
            Message = $"failed: {ex.DetailMessage}";
        }
    }

    [RelayCommand]
    private void Save()
    {
        if (!Uri.TryCreate(ServerUrl.Trim(), UriKind.Absolute, out var uri) ||
            uri.Scheme is not ("http" or "https"))
        {
            Message = "server URL must be an absolute http(s) URL";
            return;
        }
        var s = _store.Current;
        s.ServerUrl = uri.ToString().TrimEnd('/');
        s.PreferredLanguage = PreferredLanguage;
        s.HotkeysEnabled = HotkeysEnabled;
        s.Theme = Theme;
        s.AutoStartTranscription = AutoStart;
        s.VerboseLogging = VerboseLogging;
        if (HotkeyGestureTryParse(Hotkey))
        {
            s.ToggleRecordingHotkey = Hotkey;
        }
        else
        {
            Message = "hotkey format: Ctrl+Alt+Key (e.g. Ctrl+Alt+Space) — hotkey not saved";
        }
        _store.Save();
        Message = string.IsNullOrEmpty(Message) ? "saved — restart app to apply server URL" : Message + " · other settings saved";
    }

    private static bool HotkeyGestureTryParse(string text) =>
        MedicalScribe.WPF.Hotkeys.HotkeyGesture.TryParse(text, out _);
}
