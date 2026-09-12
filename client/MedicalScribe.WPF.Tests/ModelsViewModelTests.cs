/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Models screen (model hub) view-model behavior — catalog refresh, in-place
// status updates, download/delete command flows. Headless: no WPF Application
// needed for these paths (matches the other VM tests). Runs on Windows CI.
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.ViewModels;
using Xunit;

namespace MedicalScribe.WPF.Tests;

/// <summary>Scripted IApiClient stand-in (server drives everything; the fake
/// only feeds DTOs and records admin actions, like the backend fakes).</summary>
public sealed class FakeModelApiClient : IApiClient
{
    public List<ModelInfoDto> Models { get; } = [];

    public List<string> DownloadStarts { get; } = [];
    public List<string> Deletes { get; } = [];
    public Exception? ThrowOnGet { get; set; }
    public Exception? ThrowOnDownload { get; set; }

    public Task<IReadOnlyList<ModelInfoDto>> GetModelsAsync(CancellationToken ct = default)
    {
        if (ThrowOnGet is not null)
        {
            throw ThrowOnGet;
        }
        return Task.FromResult<IReadOnlyList<ModelInfoDto>>(Models.ToList());
    }

    public Task<ModelInfoDto?> GetModelAsync(string modelId, CancellationToken ct = default) =>
        Task.FromResult<ModelInfoDto?>(Models.FirstOrDefault(m => m.Id == modelId));

    public Task<ModelDownloadAcceptedDto> StartModelDownloadAsync(
        string modelId, CancellationToken ct = default)
    {
        if (ThrowOnDownload is not null)
        {
            throw ThrowOnDownload;
        }
        DownloadStarts.Add(modelId);
        return Task.FromResult(new ModelDownloadAcceptedDto(modelId, "downloading", "started"));
    }

    public Task<ModelDeletedDto> DeleteModelAsync(string modelId, CancellationToken ct = default)
    {
        Deletes.Add(modelId);
        var stillThere = Models.FirstOrDefault(m => m.Id == modelId);
        if (stillThere is not null)
        {
            stillThere = stillThere with { State = "not_installed", Progress = 0 };
            Models[Models.FindIndex(m => m.Id == modelId)] = stillThere;
        }
        return Task.FromResult(new ModelDeletedDto(modelId, true, "removed"));
    }

    // -- unused by this screen ---------------------------------------------------

    public Uri BaseAddress => new("http://localhost:8000");
    public Task<HealthDto?> GetHealthAsync(CancellationToken ct = default) => Task.FromResult<HealthDto?>(null);
    public Task<VersionDto?> GetVersionAsync(CancellationToken ct = default) => Task.FromResult<VersionDto?>(null);
    public Task<ClientManifestDto?> GetManifestAsync(CancellationToken ct = default) => Task.FromResult<ClientManifestDto?>(null);
    public Task<IReadOnlyList<ProviderInfoDto>> GetProvidersAsync(bool probeHealth = false, CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<ProviderInfoDto>>(new List<ProviderInfoDto>());
    public Task<ProviderModelCatalogDto?> GetProviderModelsAsync(string provider, string kind = "llm", CancellationToken ct = default) =>
        Task.FromResult<ProviderModelCatalogDto?>(null);
    public Task<TokenPairDto> LoginAsync(LoginRequestDto request, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<TokenPairDto> RefreshAsync(string refreshToken, CancellationToken ct = default) => throw new NotSupportedException();
    public Task LogoutAsync(CancellationToken ct = default) => Task.CompletedTask;
    public Task<PrincipalDto?> GetCurrentUserAsync(CancellationToken ct = default) => Task.FromResult<PrincipalDto?>(null);
    public Task<IReadOnlyList<ReportTemplateDto>> GetReportTemplatesAsync(CancellationToken ct = default) =>
        Task.FromResult<IReadOnlyList<ReportTemplateDto>>(new List<ReportTemplateDto>());
    public Task<string> ForkTemplateAsync(string sourceKey, string newKey, string? newName, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDraftResponseDto> CreateReportDraftAsync(ReportDraftRequestDto request, string encounterId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto?> GetReportAsync(string reportId, CancellationToken ct = default) => Task.FromResult<ReportDto?>(null);
    public Task<ReportDto> PatchReportSectionsAsync(string reportId, IReadOnlyDictionary<string, string> sections, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> AcknowledgeWarningAsync(string reportId, string warningId, string justification, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> FinalizeReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> ReopenReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> ApproveReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<ReportDto> AmendReportAsync(string reportId, CancellationToken ct = default) => throw new NotSupportedException();
    public Task<NormalizationResultDto?> NormalizeTextAsync(string text, CancellationToken ct = default) => Task.FromResult<NormalizationResultDto?>(null);
}

public class ModelsViewModelTests
{
    private static ModelInfoDto Dto(
        string id, string state, double? progress = null, bool autoConfigured = true,
        long totalBytes = 174_269_452, string role = "stt-shenava") =>
        new(
            Id: id, Name: $"Model {id}", Role: role, Description: "desc",
            License: "Apache-2.0", LicenseUrl: null, SourceRepo: "test/repo",
            Runtime: "in-process", Providers: new List<string> { "shenava" },
            State: state, Progress: progress, ReceivedBytes: 0, TotalBytes: totalBytes,
            CurrentFile: null, Error: null, InstalledAt: null,
            AutoConfigured: autoConfigured, OperatorNote: null,
            Files: new List<ModelFileInfoDto>(), InstallDir: $"models/{id}");

    [Fact]
    public async Task RefreshPopulatesRowsAndCountsInstalled()
    {
        var api = new FakeModelApiClient
        {
            Models = { Dto("a", "installed"), Dto("b", "not_installed") },
        };
        var vm = new ModelsViewModel(api);

        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal(2, vm.Models.Count);
        var ready = vm.Models.Single(m => m.Id == "a");
        Assert.Equal("Ready · auto-configured", ready.StateText);
        Assert.True(ready.CanDelete);
        Assert.False(ready.CanDownload);
        Assert.Contains("1 ready", vm.Note);
    }

    [Fact]
    public async Task RefreshUpdatesExistingRowsInPlace()
    {
        var api = new FakeModelApiClient { Models = { Dto("a", "not_installed") } };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);
        var row = vm.Models.Single();

        api.Models[0] = Dto("a", "downloading", 0.42);
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Same(row, vm.Models.Single());
        Assert.Equal("downloading", row.State);
        Assert.Equal(0.42, row.Progress!.Value);
        Assert.True(vm.AnyDownloading);
    }

    [Fact]
    public async Task RefreshDropsDecatalogedRows()
    {
        var api = new FakeModelApiClient { Models = { Dto("a", "installed"), Dto("b", "installed") } };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);

        api.Models.RemoveAt(0);
        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Equal("b", vm.Models.Single().Id);
    }

    [Fact]
    public async Task DownloadStartsPollableStateAndNotesSize()
    {
        var api = new FakeModelApiClient { Models = { Dto("a", "not_installed") } };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);
        var row = vm.Models.Single();

        await vm.DownloadCommand.ExecuteAsync(row);

        Assert.Contains("a", api.DownloadStarts);
        Assert.Equal("downloading", row.State);
        Assert.True(vm.AnyDownloading);
        Assert.Contains("174 MB", vm.Note); // human-readable size surfaced
    }

    [Fact]
    public async Task DownloadErrorSurfacesInNoteAndRow()
    {
        var api = new FakeModelApiClient
        {
            Models = { Dto("a", "not_installed") },
            ThrowOnDownload = ApiException.Network("server unreachable"),
        };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);
        var row = vm.Models.Single();

        await vm.DownloadCommand.ExecuteAsync(row);

        Assert.Equal("not_installed", row.State);
        Assert.Contains("server unreachable", vm.Note);
    }

    [Fact]
    public async Task DownloadRefusedForInstalledRows()
    {
        var api = new FakeModelApiClient { Models = { Dto("a", "installed") } };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);

        await vm.DownloadCommand.ExecuteAsync(vm.Models.Single());

        Assert.Empty(api.DownloadStarts); // CanDownload guard honored
    }

    [Fact]
    public async Task DeleteCallsServerAndRefreshes()
    {
        var api = new FakeModelApiClient { Models = { Dto("a", "installed") } };
        var vm = new ModelsViewModel(api);
        await vm.RefreshCommand.ExecuteAsync(null);

        await vm.DeleteCommand.ExecuteAsync(vm.Models.Single());

        Assert.Contains("a", api.Deletes);
        Assert.Equal("not_installed", vm.Models.Single().State);
    }

    [Fact]
    public async Task ServerErrorSurfacesAsNote()
    {
        var api = new FakeModelApiClient { ThrowOnGet = ApiException.Network("no route") };
        var vm = new ModelsViewModel(api);

        await vm.RefreshCommand.ExecuteAsync(null);

        Assert.Empty(vm.Models);
        Assert.Contains("server unreachable", vm.Note);
    }

    [Fact]
    public void RoleTextAndSizeTextAreHumanReadable()
    {
        var shenava = new ModelRowViewModel
        {
            Id = "s", Name = "n", Role = "stt-shenava", Description = "", License = "",
            SourceRepo = "", Runtime = "", AutoConfigured = true, OperatorNote = "",
            TotalBytes = 174_269_452,
        };
        Assert.Equal("STT · Persian (in-process)", shenava.RoleText);
        Assert.Equal("174 MB", shenava.SizeText);

        var llama = new ModelRowViewModel
        {
            Id = "l", Name = "n", Role = "llm-llama", Description = "", License = "",
            SourceRepo = "", Runtime = "", AutoConfigured = false, OperatorNote = "restart",
            TotalBytes = 1_561_318_368,
        };
        Assert.Equal("LLM · note drafting (llama.cpp)", llama.RoleText);
        Assert.Equal("1.6 GB", llama.SizeText);
        Assert.Equal("Not installed", llama.StateText);
    }
}
