using System;
using System.Threading.Tasks;

namespace SpeakType.Core
{
    public interface IAudioRecorder : IDisposable
    {
        event EventHandler Stopped;
        event EventHandler<float> AudioLevelChanged;
        bool IsRecording { get; }
        void Start(string outputFilePath);
        void Stop();
    }
}
