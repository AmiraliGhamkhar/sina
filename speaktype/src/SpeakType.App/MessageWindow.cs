using System;
using System.Windows;
using System.Windows.Interop;
using SpeakType.Core;

namespace SpeakType.App
{
    public class MessageWindow : Window
    {
        private readonly IHotkeyService _hotkeyService;
        private HwndSource? _source;

        public MessageWindow(IHotkeyService hotkeyService)
        {
            _hotkeyService = hotkeyService;
            this.Width = 0;
            this.Height = 0;
            this.WindowStyle = WindowStyle.None;
            this.ShowInTaskbar = false;
            this.Visibility = Visibility.Hidden;
        }

        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            var handle = new WindowInteropHelper(this).Handle;
            _source = HwndSource.FromHwnd(handle);
            _source.AddHook(HwndHook);
            
            _hotkeyService.SetWindowHandle(handle);
        }

        protected override void OnClosed(EventArgs e)
        {
            _source?.RemoveHook(HwndHook);
            _source = null;
            _hotkeyService.Unregister();
            base.OnClosed(e);
        }

        private IntPtr HwndHook(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            const int WM_HOTKEY = 0x0312;
            if (msg == WM_HOTKEY)
            {
                if (_hotkeyService is HotkeyService service)
                {
                    service.ProcessMessage(msg, wParam, lParam);
                }
            }
            return IntPtr.Zero;
        }
    }
}
