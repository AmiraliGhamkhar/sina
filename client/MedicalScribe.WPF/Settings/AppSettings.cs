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

// Client-side settings model + JSON persistence.
// ADAPTED FROM: SpeakType (MIT) src/SpeakType.Core/SettingsStore.cs &
// AppSettings.cs — same load-once/save-on-change pattern with safe fallback
// to defaults on corruption; moved to MedicalScribe paths and trimmed to the
// fields this platform needs (no clipboard/whisper paths — STT is server-side).
using System.Text.Json;
using System.Text.Json.Serialization;

namespace MedicalScribe.WPF.Settings;

public sealed class AppSettings
{
    // Connection
    public string ServerUrl { get; set; } = "http://localhost:8000";

    // Session defaults (sent in session.start; server may override via policy)
    public string PreferredLanguage { get; set; } = "fa-en";
    public string RoutingMode { get; set; } = "auto";

    // Audio (device id from WASAPI endpoint; empty = system default)
    public string MicrophoneId { get; set; } = "";

    // Dictation stream policy (client-side knobs; server may cap them)
    public bool PrivacyRequired { get; set; } // pin to privacy-safe providers
    public int ReconnectMaxAttempts { get; set; } = 5;
    public bool AutoStartTranscription { get; set; }

    // Hotkeys (parse format: modifier combinations joined with '+', e.g. "Ctrl+Alt+Space")
    public string ToggleRecordingHotkey { get; set; } = "Ctrl+Alt+Space";
    public bool HotkeysEnabled { get; set; } = true;

    // UI
    public string Theme { get; set; } = "light";

    // Diagnostics
    public bool VerboseLogging { get; set; }
}

public interface ISettingsStore
{
    AppSettings Current { get; }
    void Load();
    void Save();
    string FilePath { get; }
}

public sealed class JsonSettingsStore : ISettingsStore
{
    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public string FilePath { get; }

    public AppSettings Current { get; private set; } = new();

    public JsonSettingsStore()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "MedicalScribe");
        Directory.CreateDirectory(dir);
        FilePath = Path.Combine(dir, "settings.json");
    }

    /// <summary>For tests: point at an explicit file.</summary>
    public JsonSettingsStore(string filePath)
    {
        FilePath = filePath;
        Directory.CreateDirectory(Path.GetDirectoryName(filePath)!);
    }

    public void Load()
    {
        if (!File.Exists(FilePath))
        {
            Current = new AppSettings();
            return;
        }
        try
        {
            var json = File.ReadAllText(FilePath);
            Current = JsonSerializer.Deserialize<AppSettings>(json, Options) ?? new AppSettings();
        }
        catch (Exception)
        {
            // corrupt settings must not brick the app — reset to defaults
            Current = new AppSettings();
        }
    }

    public void Save()
    {
        try
        {
            var directory = Path.GetDirectoryName(FilePath);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }
            File.WriteAllText(FilePath, JsonSerializer.Serialize(Current, Options));
        }
        catch (IOException)
        {
            // settings save is best-effort; the UI already holds the new values
        }
    }
}
