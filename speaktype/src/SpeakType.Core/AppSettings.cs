namespace SpeakType.Core
{
    public class AppSettings
    {
        public bool IsPushToTalk { get; set; } = false;
        
        // Hotkey
        // Default: Ctrl + Alt + Space
        public bool HotkeyAlt { get; set; } = true;
        public bool HotkeyControl { get; set; } = true;
        public bool HotkeyShift { get; set; } = false;
        public bool HotkeyWin { get; set; } = false;
        public string HotkeyKey { get; set; } = "Space";

        // Paths
        public string WhisperModelPath { get; set; } = "models\\ggml-base.en.bin";
        public string WhisperPath { get; set; } = "tools\\whisper\\whisper-cli.exe";

        // Audio
        // Audio
        public int MicrophoneDeviceNumber { get; set; } = -1; // -1 = auto-detect
        public bool IsAudioFeedbackEnabled { get; set; } = true;

        // UI
        public int OverlayDisplayDurationMs { get; set; } = 0;

        // Clipboard
        public int ClipboardRetryCount { get; set; } = 5;
        public int ClipboardRetryDelayMs { get; set; } = 5;
        public int ClipboardPasteCompletionDelayMs { get; set; } = 10;
    }
}

