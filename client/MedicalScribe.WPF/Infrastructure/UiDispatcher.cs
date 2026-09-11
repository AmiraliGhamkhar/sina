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
