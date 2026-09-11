/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;

// First-run wizard (Phase 8: server URL + mic pick + hotkey notice).
// Code-behind stays glue-only: validation/probing lives in FirstRunProbe,
// persistence in ISettingsStore, device listing in IAudioCaptureService.
using MedicalScribe.WPF.Audio;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.Views;

public partial class FirstRunWindow : Window
{
    private readonly ISettingsStore _store;
    private readonly List<AudioDeviceInfo> _mics = new();
    private bool _probeOk;

    public FirstRunWindow(ISettingsStore store)
    {
        InitializeComponent();
        _store = store;
        ServerUrlInput.Text = _store.Current.ServerUrl;
        HotkeyText.Text = _store.Current.HotkeysEnabled
            ? $"Hold-to-talk toggle: {_store.Current.ToggleRecordingHotkey} (change it in Settings)"
            : "Hotkeys are disabled — recording is started from the dashboard";
        Loaded += async (_, _) => await LoadMicsAsync().ConfigureAwait(true);
    }

    private async Task LoadMicsAsync()
    {
        try
        {
            using var capture = new NAudioCaptureService(new FileLogger());
            var devices = await capture.ListDevicesAsync().ConfigureAwait(true);
            _mics.AddRange(devices);
            MicCombo.ItemsSource = _mics;
            var selected = _mics.FirstOrDefault(m => m.Id == _store.Current.MicrophoneId)
                           ?? _mics.FirstOrDefault(m => m.IsDefault)
                           ?? _mics.FirstOrDefault();
            MicCombo.SelectedItem = selected;
        }
        catch (Exception)
        {
            // device enumeration failing must never block first run — the
            // recorder view falls back to the system default device
            MicCombo.IsEnabled = false;
        }
    }

    private async void OnTestConnection(object sender, RoutedEventArgs e)
    {
        ProbeResultText.Text = "Connecting…";
        ProbeResultText.Foreground = TryFindResource("MutedInk") as Brush ?? Brushes.Gray;
        var result = await FirstRunProbe.TryConnectAsync(ServerUrlInput.Text).ConfigureAwait(true);
        _probeOk = result.Ok;
        ProbeResultText.Text = result.Detail;
        ProbeResultText.Foreground = result.Ok ? Brushes.Green : Brushes.Firebrick;
    }

    private void OnSave(object sender, RoutedEventArgs e)
    {
        if (!FirstRunProbe.TryNormalizeUrl(ServerUrlInput.Text, out var url))
        {
            MessageBox.Show(this,
                "Enter a full server URL, e.g. https://scribe.clinic.example",
                "Server URL", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        if (!_probeOk)
        {
            var go = MessageBox.Show(this,
                "The connection test did not succeed. Save anyway?",
                "Server unreachable", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (go != MessageBoxResult.Yes)
            {
                return;
            }
        }
        var s = _store.Current;
        s.ServerUrl = url;
        s.MicrophoneId = (MicCombo.SelectedItem as AudioDeviceInfo)?.Id ?? s.MicrophoneId;
        s.IsFirstRun = false;
        _store.Save();
        DialogResult = true;
    }

    private void OnSkip(object sender, RoutedEventArgs e)
    {
        // defaults are valid (http://localhost:8000 + system mic); just stop
        // asking on every boot
        _store.Current.IsFirstRun = false;
        _store.Save();
        DialogResult = false;
    }
}
