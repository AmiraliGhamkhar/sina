using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using SpeakType.Core;
using Forms = System.Windows.Forms;

namespace SpeakType.App
{
    public partial class App : System.Windows.Application
    {
        private Forms.NotifyIcon? _notifyIcon;
        private OverlayWindow? _overlay;
        
        // Services
        private IAudioRecorder? _recorder;
        private IWhisperTranscriber? _transcriber;
        private IHotkeyService? _hotkeyService;
        private IClipboardInserter? _clipboard;
        private ILogger? _logger;
        private SettingsStore? _settings;
        private IAudioService? _audioService;

        private const string AppName = "SpeakType";
        private string _appDataDir = "";
        
        // State
        private bool _isBusy;

        private void Application_Startup(object sender, StartupEventArgs e)
        {
            // Setup Paths
            _appDataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), AppName);
            var logPath = Path.Combine(_appDataDir, "logs", "app.log");
            var settingsPath = Path.Combine(_appDataDir, "settings.json");

            // Init Services
            _logger = new FileLogger(logPath);
            _settings = new SettingsStore(settingsPath);
            _settings.Load();

            _recorder = new AudioRecorder(_logger);
            _recorder.AudioLevelChanged += (s, level) => _overlay?.UpdateWaveform(level);
            _transcriber = new WhisperTranscriber();
            _hotkeyService = new HotkeyService();
            _audioService = new AudioService(_logger, _settings);
            _clipboard = new ClipboardInserter(_logger);

            // Init UI
            _overlay = new OverlayWindow();
            InitTrayIcon();

            // Register Hotkey
            var messageWindow = new MessageWindow(_hotkeyService);
            messageWindow.Show(); // It will be hidden/size 0
            
            _hotkeyService.HotkeyPressed += OnHotkeyPressed;
            RegisterHotkey();

            // Log startup
            _logger.Log("App Started");
        }

        // Icons
        private System.Drawing.Icon? _iconIdle;
        private System.Drawing.Icon? _iconActive;

        private void InitTrayIcon()
        {
            // Load Icons
            try 
            {
                var idlePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Assets", "tray_idle.ico");
                var activePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Assets", "tray_active.ico");

                if (File.Exists(idlePath)) _iconIdle = new System.Drawing.Icon(idlePath);
                else _iconIdle = System.Drawing.Icon.ExtractAssociatedIcon(System.Reflection.Assembly.GetExecutingAssembly().Location!);

                if (File.Exists(activePath)) _iconActive = new System.Drawing.Icon(activePath);
                else _iconActive = _iconIdle; // Fallback
            }
            catch (Exception ex)
            {
                _logger?.LogError("Failed to load icons, using default", ex);
                _iconIdle = System.Drawing.Icon.ExtractAssociatedIcon(System.Reflection.Assembly.GetExecutingAssembly().Location!);
                _iconActive = _iconIdle;
            }

            _notifyIcon = new Forms.NotifyIcon
            {
                Icon = _iconIdle,
                Visible = true,
                Text = "SpeakType"
            };

            var contextMenu = new Forms.ContextMenuStrip();
            contextMenu.Items.Add("Start/Stop Dictation", null, async (s, e) => await ToggleDictation());
            contextMenu.Items.Add("-");
            contextMenu.Items.Add("Settings", null, (s, e) => ShowSettings());
            contextMenu.Items.Add("Open Logs", null, (s, e) => OpenLogs());
            contextMenu.Items.Add("Exit", null, (s, e) => ExitApplication());

            _notifyIcon.ContextMenuStrip = contextMenu;
        }

        private async void OnHotkeyPressed(object? sender, EventArgs e)
        {
            await ToggleDictation();
        }

        private async Task ToggleDictation()
        {
            if (_isBusy) return;

            if (_recorder!.IsRecording)
            {
                await StopAndTranscribe();
            }
            else
            {
                StartRecording();
            }
        }

        private void StartRecording()
        {
            try
            {
                if (_notifyIcon != null) _notifyIcon.Icon = _iconActive;
                _logger?.Log("Starting recording...");
                _audioService?.PlayStartSound();
                var wavPath = Path.Combine(Path.GetTempPath(), "speaktype_audio.wav");
                _recorder?.Start(wavPath);
                _overlay?.ShowListening();
            }
            catch (Exception ex)
            {
                _logger?.LogError("Failed to start recording", ex);
                ShowError("Failed to start recording.");
            }
        }

        private async Task StopAndTranscribe()
        {
            _isBusy = true;
            try
            {
                _recorder?.Stop();
                if (_notifyIcon != null) _notifyIcon.Icon = _iconIdle;
                _audioService?.PlayStopSound();
                _overlay?.SetStatus("Transcribing...");
                _logger?.Log("Stopped recording. Transcribing...");

                var wavPath = Path.Combine(Path.GetTempPath(), "speaktype_audio.wav");
                var modelPath = ResolvePath(_settings?.Current?.WhisperModelPath ?? "models\\ggml-base.en.bin");
                var whisperPath = ResolvePath(_settings?.Current?.WhisperPath ?? "tools\\whisper\\main.exe");

                if (modelPath == null) throw new FileNotFoundException("Model file not found. Please check models/readme.md");
                if (whisperPath == null) throw new FileNotFoundException("Whisper executable not found. Please check tools/whisper/readme.md");

                var text = await _transcriber!.TranscribeAsync(wavPath, modelPath, whisperPath);
                
                _logger?.Log($"Transcription result: {text}");

                if (!string.IsNullOrWhiteSpace(text))
                {
                    _logger?.Log($"Attempting to insert text: '{text}'");
                    await _clipboard!.InsertTextAsync(text);
                    _logger?.Log("Text insertion completed");
                    _overlay?.SetStatus("Done", autoHide: true);
                }
                else
                {
                    _logger?.Log("No text to insert (empty or whitespace)");
                    _overlay?.SetStatus("No text", autoHide: true);
                }
            }
            catch (Exception ex)
            {
                _logger?.LogError("Error during transcription flow", ex);
                _overlay?.SetStatus("Error", autoHide: true);
                 _notifyIcon?.ShowBalloonTip(3000, "SpeakType Error", ex.Message, Forms.ToolTipIcon.Error);
            }
            finally
            {
                _isBusy = false;
                
                // Cleanup temp file
                try
                {
                    var wavPath = Path.Combine(Path.GetTempPath(), "speaktype_audio.wav");
                    if (File.Exists(wavPath))
                    {
                        File.Delete(wavPath);
                        _logger?.Log("[Cleanup] Deleted temp audio file");
                    }
                }
                catch (Exception ex)
                {
                    _logger?.Log($"[Cleanup] Failed to delete temp audio file: {ex.Message}");
                }
            }
        }

        private void ShowSettings()
        {
            if (_settings == null) return;
            
            var settingsWindow = new SettingsWindow(_settings);
            if (settingsWindow.ShowDialog() == true)
            {
                // Settings saved, re-register hotkey in case it changed
                _logger?.Log("Settings updated, re-registering hotkey...");
                try
                {
                    _hotkeyService?.Unregister();
                    RegisterHotkey();
                }
                catch (Exception ex)
                {
                    _logger?.LogError("Failed to re-register hotkey after settings change", ex);
                }
            }
        }

        private void RegisterHotkey()
        {
            if (_settings?.Current == null || _hotkeyService == null) return;

            var keyStr = _settings.Current.HotkeyKey;
            if (Enum.TryParse<System.Windows.Input.Key>(keyStr, out var key))
            {
                _hotkeyService.Register(
                    key,
                    _settings.Current.HotkeyControl,
                    _settings.Current.HotkeyAlt,
                    _settings.Current.HotkeyShift,
                    _settings.Current.HotkeyWin);
                _logger?.Log($"Hotkey registered: {_settings.Current.HotkeyControl}+" +
                             $"{_settings.Current.HotkeyAlt}+{_settings.Current.HotkeyShift}+" +
                             $"{_settings.Current.HotkeyWin}+{keyStr}");
            }
            else
            {
                _logger?.Log($"Invalid hotkey configured: {keyStr}");
            }
        }

        private string? ResolvePath(string path)
        {
            if (File.Exists(path)) return Path.GetFullPath(path);

            // 1. Check relative to BaseDirectory (bin folder)
            var basePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, path);
            if (File.Exists(basePath)) return basePath;

            // 2. Walk up from BaseDirectory to find "tools" or "models" at some root
            var currentDir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (int i = 0; i < 6; i++)
            {
                if (currentDir == null) break;
                var candidate = Path.Combine(currentDir.FullName, path);
                if (File.Exists(candidate)) return candidate;
                
                currentDir = currentDir.Parent;
            }

            return null;
        }

        private void OpenLogs()
        {
            var logDir = Path.Combine(_appDataDir, "logs");
            if (Directory.Exists(logDir))
            {
                System.Diagnostics.Process.Start("explorer.exe", logDir);
            }
        }

        private void ShowError(string msg)
        {
             _notifyIcon?.ShowBalloonTip(3000, "SpeakType Error", msg, Forms.ToolTipIcon.Error);
        }

        private void ExitApplication()
        {
            _logger?.Log("App Shutting Down");
            
            // Dispose services
            _recorder?.Dispose();
            _hotkeyService?.Dispose();
            _notifyIcon?.Dispose();
            _overlay?.Close();
            
            // Cleanup temp files
            try
            {
                var wavPath = Path.Combine(Path.GetTempPath(), "speaktype_audio.wav");
                if (File.Exists(wavPath))
                {
                    File.Delete(wavPath);
                }
            }
            catch { /* Best effort cleanup */ }
            
            Current.Shutdown();
        }

        private void Application_Exit(object sender, ExitEventArgs e)
        {
            _notifyIcon?.Dispose();
        }
    }
}
