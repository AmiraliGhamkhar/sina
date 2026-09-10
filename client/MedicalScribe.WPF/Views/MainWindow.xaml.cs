// View plumbing only (window handle for RegisterHotKey). No business logic —
// spec §19.7.
using System.Windows;
using System.Windows.Interop;
using MedicalScribe.WPF.ViewModels;

namespace MedicalScribe.WPF.Views;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        if (DataContext is MainViewModel mainViewModel)
        {
            mainViewModel.OnWindowHandle(new WindowInteropHelper(this).Handle);
        }
    }
}
