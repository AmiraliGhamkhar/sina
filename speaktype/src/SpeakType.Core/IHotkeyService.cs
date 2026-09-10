using System;

namespace SpeakType.Core
{
    public interface IHotkeyService : IDisposable
    {
        event EventHandler HotkeyPressed;
        void SetWindowHandle(IntPtr windowHandle);
        bool Register(System.Windows.Input.Key key, bool ctrl, bool alt, bool shift, bool win);
        void Unregister();
    }
}
