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

using MedicalScribe.WPF.Audio;
using MedicalScribe.WPF.Hotkeys;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.Settings;
using MedicalScribe.WPF.ViewModels;
using Microsoft.Extensions.DependencyInjection;

namespace MedicalScribe.WPF.Infrastructure;

public static class ServiceCollectionExtensions
{
    public static IServiceCollection AddMedicalScribeClient(this IServiceCollection services)
    {
        var settingsStore = new JsonSettingsStore();
        settingsStore.Load();

        // Both registrations point at the SAME instance. The interface
        // registration is load-bearing: DictationSession, Ai-/UserSettingsViewModel
        // and MainViewModel all take ISettingsStore — asking for it was never
        // registered, so GetRequiredService<MainViewModel>() used to throw
        // "Unable to resolve service for type ...ISettingsStore" and the app
        // died before its first window (caught by ServiceCompositionTests).
        services.AddSingleton<ISettingsStore>(settingsStore);
        services.AddSingleton(settingsStore);
        services.AddSingleton(settingsStore.Current);
        services.AddSingleton<ILoggerService, FileLogger>();
        services.AddSingleton<IUiDispatcher, WpfUiDispatcher>();

        services.AddSingleton<TokenStore>();
        services.AddSingleton<IAccessTokenSource>(sp => sp.GetRequiredService<TokenStore>());
        services.AddSingleton(sp =>
        {
            var http = new HttpClient
            {
                BaseAddress = new Uri(settingsStore.Current.ServerUrl),
            };
            return new ApiClient(http, sp.GetRequiredService<IAccessTokenSource>(), sp.GetRequiredService<ILoggerService>());
        });
        services.AddSingleton<IApiClient>(sp => sp.GetRequiredService<ApiClient>());

        services.AddSingleton<IAuthorizationService, AuthorizationService>();
        services.AddSingleton<IServerStatusService, ServerStatusService>();
        services.AddSingleton<IAudioCaptureService, NAudioCaptureService>();
        services.AddSingleton(new ReconnectPolicy(maxAttempts: settingsStore.Current.ReconnectMaxAttempts));
        services.AddSingleton<ITranscriptionStream, WsTranscriptionClient>();
        services.AddSingleton<DictationSession>();
        services.AddSingleton<IHotkeyService, Win32HotkeyService>();

        services.AddSingleton<LoginViewModel>();
        services.AddSingleton<DashboardViewModel>();
        services.AddSingleton<PatientEncounterViewModel>();
        services.AddSingleton<RecorderViewModel>();
        services.AddSingleton<LiveTranscriptViewModel>();
        services.AddSingleton<ILiveTranscriptSink>(sp => sp.GetRequiredService<LiveTranscriptViewModel>());
        services.AddSingleton<MedicalEditorViewModel>();
        services.AddSingleton<ReportViewModel>();
        services.AddSingleton<TemplatesViewModel>();
        services.AddSingleton<AiSettingsViewModel>();
        services.AddSingleton<ModelsViewModel>();
        services.AddSingleton<UserSettingsViewModel>();
        services.AddSingleton<MainViewModel>();
        return services;
    }
}
