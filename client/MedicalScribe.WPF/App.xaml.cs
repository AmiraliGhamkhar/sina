// Application composition root. Deliberately tiny: DI wiring + lifetime.
// (SpeakType, MIT, drives its whole pipeline from App.xaml.cs — we keep only
// the tray/hotkey *lifecycle* idea and push everything else into services.)
using System.Windows;
using System.Windows.Threading;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.ViewModels;
using MedicalScribe.WPF.Views;
using Microsoft.Extensions.DependencyInjection;

namespace MedicalScribe.WPF;

public partial class App : Application
{
    private ServiceProvider? _services;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        DispatcherUnhandledException += OnDispatcherUnhandledException;
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            if (args.ExceptionObject is Exception ex)
            {
                LogCrash(ex);
            }
        };

        var container = new ServiceCollection()
            .AddMedicalScribeClient()
            .BuildServiceProvider();
        _services = container;

        var mainViewModel = container.GetRequiredService<MainViewModel>();
        MainWindow = new MainWindow { DataContext = mainViewModel };
        MainWindow.Closed += (_, _) => Shutdown();
        MainWindow.Show();
    }

    private void OnDispatcherUnhandledException(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        LogCrash(e.Exception);
        e.Handled = true; // keep the clinician's session alive; crash dialogs are worse
    }

    private static void LogCrash(Exception ex)
    {
        try
        {
            var logger = new FileLogger();
            logger.Error("unhandled exception", ex);
        }
        catch
        {
            // last-resort: never throw from a crash handler
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _services?.Dispose();
        base.OnExit(e);
    }
}
