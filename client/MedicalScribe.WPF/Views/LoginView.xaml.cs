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

// The only code-behind in the app besides the window handle: PasswordBox is
// intentionally non-bindable by WPF security design; this is standard view
// plumbing (write-through to the VM property), not logic.
using System.Windows;
using System.Windows.Controls;
using MedicalScribe.WPF.ViewModels;

namespace MedicalScribe.WPF.Views;

public partial class LoginView : UserControl
{
    private bool _syncing;

    public LoginView()
    {
        InitializeComponent();
    }

    private void OnPasswordChanged(object sender, RoutedEventArgs e)
    {
        if (_syncing || DataContext is not LoginViewModel vm)
        {
            return;
        }
        _syncing = true;
        var box = (PasswordBox)sender;
        if (vm.Password != box.Password)
        {
            vm.Password = box.Password;
        }
        _syncing = false;
    }
}
