using System.Threading;
using System.Threading.Tasks;

namespace SpeakType.Core
{
    public interface IWhisperTranscriber
    {
        Task<string> TranscribeAsync(string audioFilePath, string modelPath, string whisperPath, CancellationToken cancellationToken = default);
    }
}
