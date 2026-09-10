# SpeakType Installation Guide

## For End Users

### Quick Install (Recommended)

1. **Download** the latest `SpeakTypeSetup.exe` from [GitHub Releases](https://github.com/DrNightmare/speaktype/releases)
2. **Run** the installer
3. **Follow** the installation wizard
4. **Wait** for the installer to download dependencies (whisper.cpp and AI models)
5. **Launch** SpeakType from the Start Menu or Desktop

That's it! The installer handles everything automatically.

---

## System Requirements

- **OS:** Windows 10 or Windows 11 (64-bit)
- **RAM:** 4 GB minimum, 8 GB recommended
- **Disk Space:** 500 MB for installation
- **Internet:** Required during installation to download AI models

---

## What Gets Installed?

The installer will:
- Install SpeakType application to `C:\Program Files\SpeakType`
- Download whisper.cpp binaries (~5 MB)
- Download base English AI model (~140 MB)
- Create Start Menu entry
- Optionally create Desktop shortcut
- Store user settings in `%AppData%\SpeakType`

---

## First Launch

1. **Launch** SpeakType from Start Menu or Desktop
2. **Look** for the SpeakType icon in your system tray (bottom-right)
3. **Press** `Ctrl + Alt + Space` to start dictating
4. **Speak** your text
5. **Press** `Ctrl + Alt + Space` again to stop and insert text

---

## Troubleshooting

### Installer fails to download dependencies
- Check your internet connection
- Try running the installer as Administrator
- Manually run: `C:\Program Files\SpeakType\download-dependencies.ps1`

### App doesn't start
- Check Windows Event Viewer for errors
- Ensure you have Windows 10/11 (64-bit)
- Try reinstalling

### Microphone not working
- Check Windows microphone permissions
- Go to Settings → Privacy → Microphone
- Ensure apps can access your microphone

### Transcription returns "you" or gibberish
- Check that the correct microphone is selected in Windows
- Speak clearly and close to the microphone
- Check logs: `%AppData%\SpeakType\logs\app.log`

### Hotkey doesn't work
- Another app might be using `Ctrl + Alt + Space`
- Close other apps and try again
- Future versions will allow custom hotkeys

---

## Uninstallation

1. Go to **Settings → Apps → Installed apps**
2. Find **SpeakType**
3. Click **Uninstall**

Or use the uninstaller from Start Menu: **SpeakType → Uninstall SpeakType**

---

## For Developers

See [README.md](README.md) for development setup instructions.
