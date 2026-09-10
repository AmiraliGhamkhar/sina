# SpeakType

A local-first Windows dictation app built with .NET 8 WPF and whisper.cpp.

## 📥 Download & Install (For Users)

**Latest Release:** [Download SpeakTypeSetup.exe](https://github.com/DrNightmare/speaktype/releases/latest)

1. Download `SpeakTypeSetup.exe`
2. Run the installer
3. Follow the installation wizard (it will download AI models automatically)
4. Launch from Start Menu or Desktop

See [INSTALL.md](INSTALL.md) for detailed installation instructions and troubleshooting.

---

## ✨ Features

- **Global hotkey** (`Ctrl+Alt+Space`) to start/stop recording
- **Local transcription** using whisper.cpp - no cloud, no internet required
- **Auto-insertion** of text into any application (VS Code, Browser, etc.)
- **System tray** interface for easy access
- **Automatic microphone detection** - skips virtual devices like Steam
- **Clipboard preservation** - your clipboard is restored after paste

---

## 🎯 Usage

1. **Start dictating:** Press `Ctrl+Alt+Space`
2. **Speak** your text (overlay shows "Listening...")
3. **Stop:** Press `Ctrl+Alt+Space` again
4. **Result:** Text is automatically transcribed and pasted into your active window

**Tip:** Right-click the system tray icon for options (Open Logs, Exit, etc.)

---

## 🛠️ For Developers

### Prerequisites
- Windows 10/11 (x64)
- .NET 8 SDK
- Git

### Setup

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/DrNightmare/speaktype.git
   cd speaktype
   ```

2. **Get whisper.cpp binaries:**
   - Download from [whisper.cpp releases](https://github.com/ggerganov/whisper.cpp/releases)
   - Extract ALL files to `tools/whisper/`
   - Ensure `whisper-cli.exe` and `whisper.dll` are present

3. **Get a Whisper model:**
   - Download `ggml-base.en.bin` from [Hugging Face](https://huggingface.co/ggerganov/whisper.cpp)
   - Place in `models/ggml-base.en.bin`

4. **Build and run:**
   ```powershell
   cd src
   dotnet build
   dotnet run --project SpeakType.App
   ```

### Building the Installer

1. **Install Inno Setup:** [Download here](https://jrsoftware.org/isdl.php)

2. **Build the release:**
   ```powershell
   .\build-release.ps1
   ```

3. **Compile the installer:**
   ```powershell
   iscc installer\SpeakType.iss
   ```

4. **Output:** `installer\Output\SpeakTypeSetup.exe`

---

## 📁 Architecture

- **SpeakType.Core**: Audio recording (NAudio), Transcription (whisper.cpp), Hotkeys (Win32 API), Clipboard management
- **SpeakType.App**: WPF UI, System Tray, Status Overlay, Settings

---

## 🐛 Troubleshooting

**Hotkey doesn't work:**
- Check if another app is using `Ctrl+Alt+Space`
- Check logs: `%AppData%\SpeakType\logs\app.log`

**Transcription returns gibberish:**
- Ensure the correct microphone is selected in Windows
- Check that `tools/whisper/whisper-cli.exe` and `models/ggml-base.en.bin` exist

**App doesn't start:**
- Check Windows Event Viewer for errors
- Ensure you have Windows 10/11 64-bit

See [INSTALL.md](INSTALL.md) for more troubleshooting tips.

---

## 📄 License

MIT License - see [LICENSE](installer/LICENSE.txt)

