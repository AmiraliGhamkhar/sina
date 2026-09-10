using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace SpeakType.App
{
    public partial class OverlayWindow : Window
    {
        private DispatcherTimer _hideTimer;
        private List<System.Windows.Shapes.Rectangle> _waveformBars = new List<System.Windows.Shapes.Rectangle>();
        private const double WindowWidth = 220; // Fixed width from XAML
        private const int BarCount = 12; // Fewer bars for compact look
        private Queue<float> _audioLevels = new Queue<float>();

        public OverlayWindow()
        {
            InitializeComponent();
            _hideTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
            _hideTimer.Tick += (s, e) => 
            {
                _hideTimer.Stop();
                Hide();
            };
            
            InitializeWaveform();
            Loaded += (s, e) => AlignWaveformBars();
        }

        private void InitializeWaveform()
        {
            // Firewatch Orange Gradient
            var gradient = new LinearGradientBrush
            {
                StartPoint = new System.Windows.Point(0, 0),
                EndPoint = new System.Windows.Point(0, 1)
            };
            // Vibrant Orange to Red-Orange
            gradient.GradientStops.Add(new GradientStop(System.Windows.Media.Color.FromRgb(255, 140, 0), 0.0)); // Darker Orange
            gradient.GradientStops.Add(new GradientStop(System.Windows.Media.Color.FromRgb(255, 69, 0), 1.0));  // Red/Orange

            // Pre-calculate centered positions
            double startX = (WindowWidth - (BarCount * 6)) / 2;
            double centerY = 48 / 2; // Height is 48

            for (int i = 0; i < BarCount; i++)
            {
                var bar = new System.Windows.Shapes.Rectangle
                {
                    Width = 4, 
                    Height = 4, 
                    Fill = gradient,
                    RadiusX = 2,
                    RadiusY = 2
                };
                
                // Set initial position immediately
                Canvas.SetLeft(bar, startX + (i * 6));
                Canvas.SetTop(bar, centerY - 2);

                _waveformBars.Add(bar);
                WaveformCanvas.Children.Add(bar);
            }
        }

        private void AlignWaveformBars()
        {
            double width = WaveformCanvas.ActualWidth > 0 ? WaveformCanvas.ActualWidth : WindowWidth;
            double height = WaveformCanvas.ActualHeight > 0 ? WaveformCanvas.ActualHeight : 48;

            double startX = (width - (BarCount * 6)) / 2;
            double centerY = height / 2;

            for(int i=0; i<BarCount; i++)
            {
                var bar = _waveformBars[i];
                Canvas.SetLeft(bar, startX + (i * 6));
                Canvas.SetTop(bar, centerY - (bar.Height / 2));
            }
        }


        public void UpdateWaveform(float level)
        {
            Dispatcher.Invoke(() =>
            {
                _audioLevels.Enqueue(level);
                if (_audioLevels.Count > BarCount / 2) _audioLevels.Dequeue(); // Keep half the history for mirroring

                var levels = _audioLevels.ToArray();
                // Center mirroring logic
                
                // Reset all to base state first
                foreach(var b in _waveformBars) b.Height = 4;

                // We'll map the queue to the center outwards
                // The newest item is at index 0 (center) to mirrored sides
                // Actually queue has oldest at start. Let's reverse it so [0] is newest.
                Array.Reverse(levels);

                for (int i = 0; i < levels.Length; i++)
                {
                    // Scale non-linearly for punchier look
                    double height = 4 + (Math.Pow(levels[i], 0.7) * 50); 
                    height = Math.Min(height, 28); // Cap max height

                    // Center pair
                    int leftIndex = (BarCount / 2) - 1 - i;
                    int rightIndex = (BarCount / 2) + i;

                    if (leftIndex >= 0)
                    {
                        var bar = _waveformBars[leftIndex];
                        bar.Height = height;
                        Canvas.SetTop(bar, (WaveformCanvas.ActualHeight - height) / 2);
                    }
                    if (rightIndex < BarCount)
                    {
                        var bar = _waveformBars[rightIndex];
                        bar.Height = height;
                        Canvas.SetTop(bar, (WaveformCanvas.ActualHeight - height) / 2);
                    }
                }

                // Position bars horizontally (static positions)
                double startX = (WaveformCanvas.ActualWidth - (BarCount * 6)) / 2;
                
                for(int i=0; i<BarCount; i++)
                {
                    Canvas.SetLeft(_waveformBars[i], startX + (i * 6));
                }
            });
        }

        public void ClearWaveform()
        {
            Dispatcher.Invoke(() =>
            {
                _audioLevels.Clear();
                foreach (var bar in _waveformBars)
                {
                    bar.Height = 2;
                }
            });
        }

        public void ShowListening()
        {
            Dispatcher.Invoke(() =>
            {
                StatusText.Text = ""; // No text while listening
                StatusText.Visibility = Visibility.Collapsed;
                WaveformCanvas.Visibility = Visibility.Visible;
                
                // Adjust columns to center waveform
                if (Content is Border border && border.Child is Grid grid)
                {
                    grid.ColumnDefinitions[0].Width = new GridLength(0); // Hide text column
                }

                Show();
                _hideTimer.Stop();
                
                // Force alignment update
                Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() => AlignWaveformBars()));
            });
        }

        public void SetStatus(string text, bool autoHide = false)
        {
            Dispatcher.Invoke(() => 
            {
                StatusText.Text = text;
                StatusText.Visibility = Visibility.Visible;
                WaveformCanvas.Visibility = Visibility.Collapsed; // Hide waveform
                
                // Restore columns
                if (Content is Border border && border.Child is Grid grid)
                {
                    grid.ColumnDefinitions[0].Width = GridLength.Auto;
                }

                Show();
                if (autoHide)
                {
                    _hideTimer.Stop();
                    _hideTimer.Start();
                    ClearWaveform();
                }
                else
                {
                    _hideTimer.Stop();
                }
            });
        }
        
        // Prevent focus stealing and position at bottom-center
        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            var hwnd = new System.Windows.Interop.WindowInteropHelper(this).Handle;
            int exStyle = (int)GetWindowLong(hwnd, GWL_EXSTYLE);
            SetWindowLong(hwnd, GWL_EXSTYLE, exStyle | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW);
            
            // Position at bottom-center
            var screenWidth = SystemParameters.PrimaryScreenWidth;
            var screenHeight = SystemParameters.PrimaryScreenHeight;
            Left = (screenWidth - Width) / 2;
            Top = screenHeight - Height - 100; // 100px from bottom
        }

        private const int GWL_EXSTYLE = -20;
        private const int WS_EX_NOACTIVATE = 0x08000000;
        private const int WS_EX_TOOLWINDOW = 0x00000080;

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern IntPtr SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

        [System.Runtime.InteropServices.DllImport("user32.dll")]
        private static extern int GetWindowLong(IntPtr hWnd, int nIndex);
    }
}
