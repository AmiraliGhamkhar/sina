/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Global hotkeys — RegisterHotKey/WM_HOTKEY pattern.
// ADAPTED FROM: SpeakType (MIT) src/SpeakType.Core/HotkeyService.cs.
// Improvements over the reference: multiple simultaneous registrations, a
// gesture model parseable from settings ("Ctrl+Alt+Space"), modifier masking
// per Windows docs, and explicit disposal contract.
using System.Runtime.InteropServices;
using System.Windows.Input;
using System.Windows.Interop;

namespace MedicalScribe.WPF.Hotkeys;

public readonly record struct HotkeyGesture(ModifierKeys Modifiers, Key Key)
{
    public override string ToString() =>
        $"{(Modifiers.HasFlag(ModifierKeys.Control) ? "Ctrl+" : "")}" +
        $"{(Modifiers.HasFlag(ModifierKeys.Alt) ? "Alt+" : "")}" +
        $"{(Modifiers.HasFlag(ModifierKeys.Shift) ? "Shift+" : "")}" +
        $"{(Modifiers.HasFlag(ModifierKeys.Windows) ? "Win+" : "")}{Key}";

    public static bool TryParse(string? text, out HotkeyGesture gesture)
    {
        gesture = default;
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }
        var modifiers = ModifierKeys.None;
        Key key = Key.None;
        foreach (var raw in text.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            switch (raw.ToLowerInvariant())
            {
                case "ctrl" or "control": modifiers |= ModifierKeys.Control; break;
                case "alt": modifiers |= ModifierKeys.Alt; break;
                case "shift": modifiers |= ModifierKeys.Shift; break;
                case "win" or "windows": modifiers |= ModifierKeys.Windows; break;
                default:
                    if (!Enum.TryParse(raw, ignoreCase: true, out key) || key == Key.None)
                    {
                        return false;
                    }
                    break;
            }
        }
        if (key == Key.None)
        {
            return false;
        }
        gesture = new HotkeyGesture(modifiers, key);
        return true;
    }
}

public interface IHotkeyService : IDisposable
{
    void SetWindowHandle(IntPtr handle);
    bool TryRegister(in HotkeyGesture gesture, string id, Action callback);
    bool Unregister(string id);
    void UnregisterAll();
}

public sealed class Win32HotkeyService : IHotkeyService
{
    private const uint MOD_ALT = 0x0001;
    private const uint MOD_CONTROL = 0x0002;
    private const uint MOD_SHIFT = 0x0004;
    private const uint MOD_WIN = 0x0008;
    /// <summary>RegisterHotKey doc: client apps should mask this for low-level hooks.</summary>
    private const uint MOD_NOREPEAT = 0x4000;
    private const int WM_HOTKEY = 0x0312;
    /// <summary>RegisterHotKey ids must be in 0x0000–0xBFFF; "MS" flavored.</summary>
    private const int IdBase = 0x4D50;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

    [DllImport("user32.dll")]
    private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    private readonly Dictionary<string, (int Id, Action Callback)> _entries = new(StringComparer.Ordinal);
    private IntPtr _hwnd;
    private HwndSource? _source;
    private int _nextId;

    public void SetWindowHandle(IntPtr handle)
    {
        if (_hwnd == handle)
        {
            return;
        }
        DetachHook();
        _hwnd = handle;
        if (handle != IntPtr.Zero)
        {
            _source = HwndSource.FromHwnd(handle);
            _source?.AddHook(WndProc);
        }
    }

    public bool TryRegister(in HotkeyGesture gesture, string id, Action callback)
    {
        if (_hwnd == IntPtr.Zero)
        {
            return false;
        }
        if (_entries.ContainsKey(id))
        {
            Unregister(id);
        }
        var modifiers = (uint)gesture.Modifiers | MOD_NOREPEAT;
        var vk = (uint)KeyInterop.VirtualKeyFromKey(gesture.Key);
        var hotkeyId = IdBase + _nextId++;
        if (!RegisterHotKey(_hwnd, hotkeyId, modifiers, vk))
        {
            return false;
        }
        _entries[id] = (hotkeyId, callback);
        return true;
    }

    public bool Unregister(string id)
    {
        if (!_entries.Remove(id, out var entry))
        {
            return false;
        }
        if (_hwnd != IntPtr.Zero)
        {
            UnregisterHotKey(_hwnd, entry.Id);
        }
        return true;
    }

    public void UnregisterAll()
    {
        foreach (var (id, _) in _entries.ToList())
        {
            Unregister(id);
        }
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg == WM_HOTKEY)
        {
            var id = wParam.ToInt32();
            foreach (var entry in _entries.Values)
            {
                if (entry.Id == id)
                {
                    entry.Callback();
                    handled = true;
                    break;
                }
            }
        }
        return IntPtr.Zero;
    }

    private void DetachHook()
    {
        _source?.RemoveHook(WndProc);
        _source = null;
    }

    public void Dispose()
    {
        UnregisterAll();
        DetachHook();
        _hwnd = IntPtr.Zero;
    }
}
