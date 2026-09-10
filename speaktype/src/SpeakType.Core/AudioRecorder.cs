using System;
using NAudio.Wave;
using System.IO;

namespace SpeakType.Core
{
    public class AudioRecorder : IAudioRecorder, IDisposable
    {
        private WaveInEvent? _waveIn;
        private WaveFileWriter? _writer;
        private string? _currentFilePath;
        private bool _isRecording;
        private long _totalBytesRecorded = 0;
        private readonly ILogger? _logger;
        
        public event EventHandler? Stopped;
        public event EventHandler<float>? AudioLevelChanged;

        public bool IsRecording => _isRecording;

        public AudioRecorder(ILogger? logger = null)
        {
            _logger = logger;
        }

        public void Start(string outputFilePath)
        {
            if (_isRecording) return;

            _currentFilePath = outputFilePath;
            _totalBytesRecorded = 0;
            
            // Ensure directory exists
            var dir = Path.GetDirectoryName(outputFilePath);
            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }

            _logger?.Log($"[AudioRecorder] Starting recording to: {outputFilePath}");
            
            // List all available devices
            AudioDeviceHelper.ListDevices(_logger!);
            
            _waveIn = new WaveInEvent();
            
            // Try to find the best microphone (not Steam)
            var deviceNumber = AudioDeviceHelper.FindBestMicrophoneDevice() ?? 0;
            _waveIn.DeviceNumber = deviceNumber;
            _waveIn.WaveFormat = new WaveFormat(16000, 1); // 16kHz Mono for Whisper
            
            _logger?.Log($"[AudioRecorder] Device #{_waveIn.DeviceNumber}: {WaveInEvent.GetCapabilities(_waveIn.DeviceNumber).ProductName}");
            _logger?.Log($"[AudioRecorder] Format: {_waveIn.WaveFormat.SampleRate}Hz, {_waveIn.WaveFormat.Channels} channels");
            
            _writer = new WaveFileWriter(outputFilePath, _waveIn.WaveFormat);

            _waveIn.DataAvailable += OnDataAvailable;
            _waveIn.RecordingStopped += OnRecordingStopped;

            _waveIn.StartRecording();
            _isRecording = true;
            _logger?.Log("[AudioRecorder] Recording started");
        }

        public void Stop()
        {
            if (!_isRecording) return;
            
            _logger?.Log($"[AudioRecorder] Stopping recording. Total bytes captured: {_totalBytesRecorded}");
            _waveIn?.StopRecording();
            // _isRecording set to false in OnRecordingStopped
        }

        private void OnDataAvailable(object? sender, WaveInEventArgs e)
        {
            if (_writer != null)
            {
                _writer.Write(e.Buffer, 0, e.BytesRecorded);
                _totalBytesRecorded += e.BytesRecorded;
                
                // Calculate audio level (RMS) for waveform visualization
                float sum = 0;
                for (int i = 0; i < e.BytesRecorded; i += 2)
                {
                    if (i + 1 < e.BytesRecorded)
                    {
                        short sample = (short)((e.Buffer[i + 1] << 8) | e.Buffer[i]);
                        float normalized = sample / 32768f;
                        sum += normalized * normalized;
                    }
                }
                float rms = (float)Math.Sqrt(sum / (e.BytesRecorded / 2));
                AudioLevelChanged?.Invoke(this, rms);
                
                if (_totalBytesRecorded % 16000 == 0) // Log every ~1 second at 16kHz
                {
                    // Log every second (commented out to reduce log spam)
                    // _logger?.Log($"[AudioRecorder] Captured {_totalBytesRecorded} bytes so far...");
                }
                
                if (_writer.Position > _writer.Length)
                {
                   _writer.Flush();
                }
            }
        }

        private void OnRecordingStopped(object? sender, StoppedEventArgs e)
        {
            _logger?.Log($"[AudioRecorder] Recording stopped. Final size: {_totalBytesRecorded} bytes");
            _isRecording = false;
            
            _writer?.Dispose();
            _writer = null;
            
            _waveIn?.Dispose();
            _waveIn = null;

            if (e.Exception != null)
            {
                _logger?.LogError($"[AudioRecorder] ERROR during recording", e.Exception);
            }

            Stopped?.Invoke(this, EventArgs.Empty);
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
