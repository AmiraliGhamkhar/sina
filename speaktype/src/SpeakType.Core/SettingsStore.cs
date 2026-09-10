using System;
using System.IO;
using System.Text.Json;

namespace SpeakType.Core
{
    public class SettingsStore
    {
        private readonly string _filePath;
        public AppSettings Current { get; private set; }

        public SettingsStore(string filePath)
        {
            _filePath = filePath;
            Current = new AppSettings();
        }

        public void Load()
        {
            if (File.Exists(_filePath))
            {
                try
                {
                    var json = File.ReadAllText(_filePath);
                    var settings = JsonSerializer.Deserialize<AppSettings>(json);
                    if (settings != null)
                    {
                        Current = settings;
                    }
                }
                catch
                {
                    // Fallback to default
                }
            }
        }

        public void Save()
        {
            try
            {
                var dir = Path.GetDirectoryName(_filePath);
                if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

                var json = JsonSerializer.Serialize(Current, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_filePath, json);
            }
            catch { }
        }
    }
}
