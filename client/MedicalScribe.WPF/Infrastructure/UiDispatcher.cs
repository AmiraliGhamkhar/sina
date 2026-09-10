// Marshalling abstraction so ViewModels/services never import WPF dispatcher
// types (keeps them unit-testable off the UI thread).
using System.Windows;

namespace MedicalScribe.WPF.Infrastructure;

public interface IUiDispatcher
{
    void Post(Action action);
    bool CheckAccess();
}

public sealed class WpfUiDispatcher : IUiDispatcher
{
    public void Post(Action action)
    {
        var dispatcher = Application.Current?.Dispatcher;
        if (dispatcher is null || dispatcher.CheckAccess())
        {
            action();
            return;
        }
        dispatcher.BeginInvoke(action);
    }

    public bool CheckAccess() => Application.Current?.Dispatcher.CheckAccess() ?? true;
}
