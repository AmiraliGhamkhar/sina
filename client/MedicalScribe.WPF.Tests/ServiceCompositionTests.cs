// Phase 8 audit — DI composition + clean-shutdown invariants.
// Resolving MainViewModel eagerly materializes the whole client graph
// (dictation session → WS stream, capture, status poller, all screens), so a
// composition failure here is exactly a "GUI never appears" failure at startup.
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.ViewModels;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class ServiceCompositionTests
{
    [Fact]
    public void MainViewModelResolvesWithWholeGraphAsSingleton()
    {
        using var provider = new ServiceCollection()
            .AddMedicalScribeClient()
            .BuildServiceProvider();

        var first = provider.GetRequiredService<MainViewModel>();
        var second = provider.GetRequiredService<MainViewModel>();

        Assert.Same(first, second);
        Assert.NotNull(first.Login);
        Assert.NotNull(first.Recorder);
        Assert.NotNull(first.LiveTranscript);
        Assert.NotEmpty(first.NavItems);
        // unauthenticated start must land on the login screen
        Assert.Same(first.Login, first.CurrentViewModel);
    }

    [Fact]
    public void SyncDisposeOfProviderDoesNotThrowAfterFullResolution()
    {
        // Regression: WsTranscriptionClient used to implement ONLY
        // IAsyncDisposable; ServiceProvider.Dispose() (sync, called from
        // App.OnExit) throws InvalidOperationException for async-only
        // services — every normal application exit crashed on teardown.
        var provider = new ServiceCollection()
            .AddMedicalScribeClient()
            .BuildServiceProvider();
        _ = provider.GetRequiredService<MainViewModel>(); // forces WS client resolution

        var ex = Record.Exception(() => provider.Dispose());

        Assert.Null(ex);
    }
}
