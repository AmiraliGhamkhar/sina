using System.Threading.Tasks;

namespace SpeakType.Core
{
    public interface IAudioService
    {
        void PlayStartSound();
        void PlayStopSound();
        void PlaySuccessSound();
    }
}
