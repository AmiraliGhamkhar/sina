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
