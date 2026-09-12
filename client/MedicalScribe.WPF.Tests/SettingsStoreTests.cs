// Phase 8 audit — settings-store resilience matrix (spec: a corrupt/inaccessible
// settings file must never permanently brick startup). Pure logic, no WPF deps.
using MedicalScribe.WPF.Settings;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class SettingsStoreTests
{
    private static string FreshPath() =>
        Path.Combine(
            Path.GetTempPath(),
            "ms-settings-tests",
            Guid.NewGuid().ToString("N"),
            "settings.json");

    [Fact]
    public void LoadWithoutFileReturnsDefaults()
    {
        var store = new JsonSettingsStore(FreshPath());
        store.Load();

        Assert.True(store.Current.IsFirstRun);
        Assert.Equal("http://localhost:8000", store.Current.ServerUrl);
        Assert.Equal("fa-en", store.Current.PreferredLanguage);
    }

    [Fact]
    public void SaveThenLoadRoundTripsValues()
    {
        var path = FreshPath();
        var store = new JsonSettingsStore(path);
        store.Load();
        store.Current.ServerUrl = "https://scribe.clinic.example";
        store.Current.ToggleRecordingHotkey = "Ctrl+Alt+R";
        store.Current.IsFirstRun = false;
        store.Current.PrivacyRequired = true;
        store.Save();

        var reloaded = new JsonSettingsStore(path);
        reloaded.Load();

        Assert.Equal("https://scribe.clinic.example", reloaded.Current.ServerUrl);
        Assert.Equal("Ctrl+Alt+R", reloaded.Current.ToggleRecordingHotkey);
        Assert.False(reloaded.Current.IsFirstRun);
        Assert.True(reloaded.Current.PrivacyRequired);
    }

    [Fact]
    public void MalformedJsonFallsBackToDefaultsWithoutThrowing()
    {
        var path = FreshPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, "{ not json at all");

        var store = new JsonSettingsStore(path);
        var ex = Record.Exception(() => store.Load());

        Assert.Null(ex);
        Assert.True(store.Current.IsFirstRun);
        Assert.Equal("http://localhost:8000", store.Current.ServerUrl);
    }

    [Fact]
    public void EmptyFileFallsBackToDefaultsWithoutThrowing()
    {
        var path = FreshPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, string.Empty);

        var store = new JsonSettingsStore(path);
        var ex = Record.Exception(() => store.Load());

        Assert.Null(ex);
        Assert.True(store.Current.IsFirstRun);
    }

    [Fact]
    public void EmptyObjectKeepsPropertyDefaults()
    {
        // missing properties must take the model defaults, not null/zero
        var path = FreshPath();
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, "{}");

        var store = new JsonSettingsStore(path);
        store.Load();

        Assert.Equal("http://localhost:8000", store.Current.ServerUrl);
        Assert.Equal("Ctrl+Alt+Space", store.Current.ToggleRecordingHotkey);
        Assert.True(store.Current.HotkeysEnabled);
        Assert.True(store.Current.IsFirstRun);
    }

    [Fact]
    public void SaveIsAtomicAndLeavesNoTempFileBehind()
    {
        var path = FreshPath();
        var store = new JsonSettingsStore(path);
        store.Load();
        store.Current.ServerUrl = "http://localhost:8001";
        store.Save();

        Assert.True(File.Exists(path));
        // no stranded sibling from the temp-write + replace step
        Assert.Empty(Directory.GetFiles(Path.GetDirectoryName(path)!, "*.tmp"));
        // and the surviving file is valid JSON
        using var doc = System.Text.Json.JsonDocument.Parse(File.ReadAllText(path));
        Assert.True(doc.RootElement.ValueKind == System.Text.Json.JsonValueKind.Object);
    }

    [Fact]
    public void SaveDoesNotThrowWhenExistingFileIsReadOnly()
    {
        // inaccessible/locked settings target: save is best-effort, never fatal
        var path = FreshPath();
        var store = new JsonSettingsStore(path);
        store.Load();
        store.Save();
        File.SetAttributes(path, FileAttributes.ReadOnly);
        try
        {
            store.Current.ServerUrl = "http://localhost:8002";
            var ex = Record.Exception(() => store.Save());
            Assert.Null(ex);
        }
        finally
        {
            File.SetAttributes(path, FileAttributes.Normal);
        }
    }
}
