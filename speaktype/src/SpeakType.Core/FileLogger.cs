using System;
using System.IO;

namespace SpeakType.Core
{
    public class FileLogger : ILogger
    {
        private readonly string _logPath;
        private readonly object _lock = new object();

        public FileLogger(string logPath)
        {
            _logPath = logPath;
            var dir = Path.GetDirectoryName(_logPath);
            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }
        }

        public void Log(string message)
        {
            Write($"[INFO] {DateTime.Now:yyyy-MM-dd HH:mm:ss} - {message}");
        }

        public void LogError(string message, Exception? ex = null)
        {
            var msg = $"[ERROR] {DateTime.Now:yyyy-MM-dd HH:mm:ss} - {message}";
            if (ex != null)
            {
                msg += $"\n{ex}";
            }
            Write(msg);
        }

        private void Write(string text)
        {
            lock (_lock)
            {
                try
                {
                    File.AppendAllText(_logPath, text + Environment.NewLine);
                }
                catch { /* Best effort logging */ }
            }
        }
    }
}
