// Local file logger.
// ADAPTED FROM: SpeakType (MIT, © SpeakType authors) src/SpeakType.Core/FileLogger.cs
// — same idea (append to %LOCALAPPDATA% rolling file), rewritten: async-safe
// single-writer queue, structured levels, and a strict rule that transcripts
// and tokens are never logged (medical data hygiene, spec §17).
namespace MedicalScribe.WPF.Infrastructure;

public interface ILoggerService
{
    void Info(string message);
    void Warn(string message);
    void Error(string message, Exception? exception = null);
    string LogFilePath { get; }
}

public sealed class FileLogger : ILoggerService, IDisposable
{
    private readonly object _gate = new();
    private StreamWriter? _writer;
    private long _bytesWritten;
    private const long RotateBytes = 5 * 1024 * 1024;

    public string LogFilePath { get; }

    public FileLogger()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "MedicalScribe", "logs");
        Directory.CreateDirectory(dir);
        LogFilePath = Path.Combine(dir, "client.log");
    }

    public void Info(string message) => Write("INFO", message);

    public void Warn(string message) => Write("WARN", message);

    public void Error(string message, Exception? exception = null)
    {
        var text = exception is null ? message : $"{message} | {exception.GetType().Name}: {Sanitize(exception.Message)}";
        Write("ERROR", text);
    }

    private void Write(string level, string message)
    {
        var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss.fff} {level,-5} {Sanitize(message)}";
        try
        {
            lock (_gate)
            {
                EnsureWriter();
                _writer!.WriteLine(line);
                _bytesWritten += line.Length + 1;
                if (_bytesWritten > RotateBytes)
                {
                    _writer.Flush();
                    _writer.Dispose();
                    File.Move(LogFilePath, LogFilePath + ".1", overwrite: true);
                    _writer = null;
                    _bytesWritten = 0;
                }
            }
        }
        catch (IOException)
        {
            // logging must never crash the editor loop
        }
    }

    private void EnsureWriter()
    {
        _writer ??= new StreamWriter(LogFilePath, append: true) { AutoFlush = false };
    }

    /// <summary>Redact anything that looks like a bearer token. Transcript text
    /// must not be routed through the logger at all — this is a backstop.</summary>
    private static string Sanitize(string input) =>
        System.Text.RegularExpressions.Regex.Replace(
            input, "Bearer\\s+[A-Za-z0-9\\-._~+/]+=*", "Bearer [REDACTED]");

    public void Dispose()
    {
        lock (_gate)
        {
            _writer?.Flush();
            _writer?.Dispose();
            _writer = null;
        }
    }
}
