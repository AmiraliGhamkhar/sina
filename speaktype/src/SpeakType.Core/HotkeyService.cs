using System;
using System.Runtime.InteropServices;

namespace SpeakType.Core
{
    public class HotkeyService : IHotkeyService
    {
        public event EventHandler? HotkeyPressed;
        private IntPtr _hwnd;
        private int _id = 9000; // Arbitrary ID
        private bool _isRegistered;

        // Modifiers: Alt = 1, Ctrl = 2, Shift = 4, Win = 8
        // Default: Ctrl + Alt + Space
        private const uint MOD_ALT = 0x0001;
        private const uint MOD_CONTROL = 0x0002;
        private const uint MOD_SHIFT = 0x0004;
        private const uint MOD_WIN = 0x0008;

        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        public void SetWindowHandle(IntPtr windowHandle)
        {
            _hwnd = windowHandle;
        }

        public bool Register(System.Windows.Input.Key key, bool ctrl, bool alt, bool shift, bool win)
        {
            if (_hwnd == IntPtr.Zero) throw new InvalidOperationException("Window handle must be set before registering hotkey.");
            if (_isRegistered) Unregister();

            uint modifiers = 0;
            if (ctrl) modifiers |= MOD_CONTROL;
            if (alt) modifiers |= MOD_ALT;
            if (shift) modifiers |= MOD_SHIFT;
            if (win) modifiers |= MOD_WIN;

            uint vk = (uint)System.Windows.Input.KeyInterop.VirtualKeyFromKey(key);

            _isRegistered = RegisterHotKey(_hwnd, _id, modifiers, vk);
            return _isRegistered;
        }

        public void Unregister()
        {
            if (_isRegistered && _hwnd != IntPtr.Zero)
            {
                UnregisterHotKey(_hwnd, _id);
                _isRegistered = false;
            }
        }

        public void ProcessMessage(int msg, IntPtr wParam, IntPtr lParam)
        {
            const int WM_HOTKEY = 0x0312;
            if (msg == WM_HOTKEY && wParam.ToInt32() == _id)
            {
                HotkeyPressed?.Invoke(this, EventArgs.Empty);
            }
        }

        public void Dispose()
        {
            Unregister();
        }
    }
}
