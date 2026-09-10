# SpeakType Roadmap & TODO

This document tracks planned features, improvements, and polishes for future versions of SpeakType.

## 🚀 High Priority (Phase 2)

- [x] **Settings UI:** A proper Windows window to configure the app (integrated into Tray Menu).
- [x] **Custom Hotkeys:** Allow users to change the global start/stop hotkey (dynamic re-registration).
- [ ] **Model Selection:** UI to download and switch between different Whisper models (Tiny, Base, Small, Medium).
- [ ] **GPU Acceleration:** Add support for `whisper.cpp` GPU backends (CUDA/CoreML) to make transcription near-instant.
- [ ] **Multi-language Support:** Allow transcribing languages other than English.

## 🎨 UI & UX Polish

- [x] **Status Overlay Improvements:**
    - [x] Repositioned to bottom-center of screen.
    - [x] Removed debug text preview for a cleaner look.
    - [x] **Premium Capsule Design:** Dark themed pill shape (#151515) with soft shadows and 24px radius.
    - [x] **Real-time Waveform:** Centered "mirroring" visualization with Firewatch Orange gradient.
    - [x] **Context-Aware Layout:** Hides prompt text during recording; perfectly centered status messages.
    - [ ] Add a "Cancel" button/gesture.
- [ ] **First-Run Experience:** A "Getting Started" guide that opens after installation.
- [x] **Audio Feedback:** Optional subtle sound effects when recording starts, stops, or finishes.
- [ ] **Tray Icon Animation:** Change icon state/color while recording.

## ⚙️ Core Enhancements

- [x] **Clipboard Fallback:** Transcribed text is now left on the clipboard after pasting, allowing for manual correction if auto-paste fails.
- [ ] **Smart Formatting:** Use a local LLM (like Llama/Mistral via Ollama or llama.cpp) to automatically fix grammar and punctuation.
- [ ] **Context-Aware Punctuation:** Better handling of "Comma", "New line", and "Period" voice commands.
- [ ] **Auto-Update:** Implement an automatic updater so users don't have to download new `.exe` files manually.
- [ ] **Clipboard Improvement:** Support non-text data types (keeping images/formatting when restoring clipboard).
- [ ] **Advanced Audio Helper:** Allow users to manually pick their microphone if auto-detection picks the wrong one.

## 🛠️ Internal & Technical

- [x] **Dev Workflow Scripts:** Created `dev.ps1` (with process termination) and `build.ps1` for rapid development.
- [ ] **Unit Tests:** Add tests for `AudioDeviceHelper` and `PathResolution`.
- [ ] **Logging View:** Add a simple log viewer window within the app.
- [ ] **Dependency Management:** Better bundling of `whisper.dll` to avoid path issues on custom installs.
- [ ] **Battery Efficiency:** Ensure background processes (like hotkey listeners) use 0% CPU when idle.

---

*Found a bug or have a suggestion? Open an issue on [GitHub](https://github.com/DrNightmare/speaktype/issues).*
