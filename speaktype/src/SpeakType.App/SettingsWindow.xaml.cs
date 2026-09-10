using System.Windows;
using Microsoft.Win32;
using SpeakType.Core;

namespace SpeakType.App
{
    public partial class SettingsWindow : Window
    {
        private readonly SettingsStore _settingsStore;
        private readonly AppSettings _currentSettings;

        public SettingsWindow(SettingsStore settingsStore)
        {
            InitializeComponent();
            _settingsStore = settingsStore;
            _currentSettings = settingsStore.Current;

            LoadSettings();
        }

        private void LoadSettings()
        {
            ChkCtrl.IsChecked = _currentSettings.HotkeyControl;
            ChkAlt.IsChecked = _currentSettings.HotkeyAlt;
            ChkShift.IsChecked = _currentSettings.HotkeyShift;
            ChkWin.IsChecked = _currentSettings.HotkeyWin;
            TxtKey.Text = _currentSettings.HotkeyKey;

            TxtWhisperPath.Text = _currentSettings.WhisperPath;
            TxtModelPath.Text = _currentSettings.WhisperModelPath;
            TxtMicIndex.Text = _currentSettings.MicrophoneDeviceNumber.ToString();
            ChkAudioFeedback.IsChecked = _currentSettings.IsAudioFeedbackEnabled;
        }

        private void BrowseWhisper_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Executables (*.exe)|*.exe|All files (*.*)|*.*",
                Title = "Select Whisper Executable"
            };

            if (dialog.ShowDialog() == true)
            {
                TxtWhisperPath.Text = dialog.FileName;
            }
        }

        private void BrowseModel_Click(object sender, RoutedEventArgs e)
        {
            var dialog = new Microsoft.Win32.OpenFileDialog
            {
                Filter = "Model files (*.bin)|*.bin|All files (*.*)|*.*",
                Title = "Select Whisper Model File"
            };

            if (dialog.ShowDialog() == true)
            {
                TxtModelPath.Text = dialog.FileName;
            }
        }

        private void Save_Click(object sender, RoutedEventArgs e)
        {
            _currentSettings.HotkeyControl = ChkCtrl.IsChecked ?? false;
            _currentSettings.HotkeyAlt = ChkAlt.IsChecked ?? false;
            _currentSettings.HotkeyShift = ChkShift.IsChecked ?? false;
            _currentSettings.HotkeyWin = ChkWin.IsChecked ?? false;
            _currentSettings.HotkeyKey = TxtKey.Text.Trim();

            _currentSettings.WhisperPath = TxtWhisperPath.Text.Trim();
            _currentSettings.WhisperModelPath = TxtModelPath.Text.Trim();

            if (int.TryParse(TxtMicIndex.Text, out int micIndex))
            {
                _currentSettings.MicrophoneDeviceNumber = micIndex;
            }

            _currentSettings.IsAudioFeedbackEnabled = ChkAudioFeedback.IsChecked ?? true;

            _settingsStore.Save();
            DialogResult = true;
            Close();
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }
    }
}
