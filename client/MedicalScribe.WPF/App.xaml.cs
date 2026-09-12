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

// Application composition root. Deliberately tiny: DI wiring + lifetime.
// (SpeakType, MIT, drives its whole pipeline from App.xaml.cs — we keep only
// the tray/hotkey *lifecycle* idea and push everything else into services.)
using System.Windows;
using System.Windows.Threading;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Settings;
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

        // Take over shutdown management while only transient windows (the
        // first-run wizard) exist. With the default OnLastWindowClose,
        // CLOSING THE WIZARD queues an application shutdown — OnStartup then
        // continues, shows MainWindow for a frame, and the queued shutdown
        // tears it right back down. That is the classic "I ran the exe and no
        // GUI ever appears" symptom.
        ShutdownMode = ShutdownMode.OnExplicitShutdown;

        try
        {
            // Phase 8 first-run wizard: runs BEFORE the container is built so the
            // HttpClient gets the saved base address (no post-hoc re-wiring). Any
            // wizard failure falls through to normal startup with defaults.
            try
            {
                var preStore = new JsonSettingsStore();
                preStore.Load();
                if (preStore.Current.IsFirstRun)
                {
                    var wizard = new FirstRunWindow(preStore);
                    wizard.ShowDialog();
                }
            }
            catch (Exception ex)
            {
                LogCrash(ex);
            }

            var container = new ServiceCollection()
                .AddMedicalScribeClient()
                .BuildServiceProvider();
            _services = container;

            var mainViewModel = container.GetRequiredService<MainViewModel>();
            MainWindow = new MainWindow { DataContext = mainViewModel };
            // The shell's lifetime IS the app's lifetime from here on.
            ShutdownMode = ShutdownMode.OnMainWindowClose;
            MainWindow.Show();
        }
        catch (Exception ex)
        {
            // Anything that throws before the first real window (DI wiring,
            // XAML load, settings store) used to exit the process with no
            // window, no message and no log entry. Surface it instead.
            LogCrash(ex);
            MessageBox.Show(
                "MedicalScribe failed to start.\n\n"
                + ex.GetType().Name + ": " + ex.Message
                + "\n\nDetails were written to:\n" + TryGetLogPath(),
                "MedicalScribe — startup failed",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            Shutdown(1);
        }
    }

    private static string TryGetLogPath()
    {
        try
        {
            return new FileLogger().LogFilePath;
        }
        catch
        {
            return "%LOCALAPPDATA%\\MedicalScribe\\logs\\client.log";
        }
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
