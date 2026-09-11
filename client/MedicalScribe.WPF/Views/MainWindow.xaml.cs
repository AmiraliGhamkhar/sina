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
