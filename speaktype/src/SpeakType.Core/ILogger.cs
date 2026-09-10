namespace SpeakType.Core
{
    public interface ILogger
    {
        void Log(string message);
        void LogError(string message, System.Exception? ex = null);
    }
}
