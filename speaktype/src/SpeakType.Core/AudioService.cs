using System;
using System.IO;
using System.Threading.Tasks;
using NAudio.Wave;
using NAudio.Wave.SampleProviders;

namespace SpeakType.Core
{
    public class AudioService : IAudioService
    {
        private readonly ILogger _logger;
        private readonly SettingsStore _settings;

        public AudioService(ILogger logger, SettingsStore settings)
        {
            _logger = logger;
            _settings = settings;
        }

        public void PlayStartSound()
        {
            if (!_settings.Current.IsAudioFeedbackEnabled) return;
            if (!PlayWav("start.wav"))
            {
                PlayTone(400, 600, 150);
            }
        }

        public void PlayStopSound()
        {
            if (!_settings.Current.IsAudioFeedbackEnabled) return;
            if (!PlayWav("stop.wav"))
            {
                PlayTone(600, 400, 150);
            }
        }

        public void PlaySuccessSound()
        {
            if (!_settings.Current.IsAudioFeedbackEnabled) return;
            Task.Run(() =>
            {
                try
                {
                    // Double blip for success
                    PlayToneInternal(800, 100);
                    Task.Delay(50).Wait();
                    PlayToneInternal(1200, 100);
                }
                catch (Exception ex)
                {
                    _logger.LogError("Failed to play success sound", ex);
                }
            });
        }

        private void PlayTone(double startFreq, double endFreq, int durationMs)
        {
            Task.Run(() => PlayToneInternal(startFreq, endFreq, durationMs));
        }

        private void PlayToneInternal(double frequency, int durationMs)
        {
             try
            {
                var signal = new SignalGenerator()
                {
                    Gain = 0.2,
                    Frequency = frequency,
                    Type = SignalGeneratorType.Sin
                }.Take(TimeSpan.FromMilliseconds(durationMs));

                using (var wo = new WaveOutEvent())
                {
                    wo.Init(signal);
                    wo.Play();
                    while (wo.PlaybackState == PlaybackState.Playing)
                    {
                        System.Threading.Thread.Sleep(50);
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError("Failed to play tone", ex);
            }
        }

        private void PlayToneInternal(double startFreq, double endFreq, int durationMs)
        {
            try
            {
                // Simple sweep simulation by playing the start freq
                // For a true sweep we'd need a custom ISampleProvider, but a simple beep is fine for now
                // or we can just pick the mid point or end point
                
                // Let's implement a very basic sweep using SignalGenerator if possible?
                // NAudio's SignalGenerator is fixed frequency. 
                // We'll stick to a simple tone for now or just play the end freq to keep it simple.
                // Actually, let's play the 'end' frequency for a cleaner "confirm" sound vs "start" sound.
                // Or:
                // Start: Low Pitch => "On"
                // Stop: High Pitch => "Off"
                
                // Update: Let's just play a single nice tone
                
                var signal = new SignalGenerator()
                {
                    Gain = 0.2, // Subtle volume
                    Frequency = startFreq, // Just play the start freq for now
                    Type = SignalGeneratorType.Sin
                }.Take(TimeSpan.FromMilliseconds(durationMs));

                using (var wo = new WaveOutEvent())
                {
                    wo.Init(signal);
                    wo.Play();
                    while (wo.PlaybackState == PlaybackState.Playing)
                    {
                        System.Threading.Thread.Sleep(50);
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError($"Failed to play tone {startFreq}", ex);
            }
        }
        private bool PlayWav(string fileName)
        {
            try
            {
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Assets", "sounds", fileName);
                if (!File.Exists(path)) return false;

                Task.Run(() =>
                {
                    try
                    {
                        using (var audioFile = new AudioFileReader(path))
                        using (var outputDevice = new WaveOutEvent())
                        {
                            outputDevice.Init(audioFile);
                            outputDevice.Play();
                            while (outputDevice.PlaybackState == PlaybackState.Playing)
                            {
                                System.Threading.Thread.Sleep(50);
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError($"Error playing wav file {fileName}", ex);
                    }
                });
                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError($"Error in PlayWav for {fileName}", ex);
                return false;
            }
        }
    }
}
