// Report screen: generated draft (editable) → clinician review → explicit
// finalize/approve. The lifecycle (draft → finalized → approved) and the hard
// rule "AI output is never automatically final" (spec §5/§8) are reflected in
// button availability even before the backend generation endpoint exists
// (Phase 6): Generate stays disabled until the server advertises the feature.
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public enum ReportState
{
    NoDraft,
    Draft,
    NeedsReview,
    Finalized,
    Approved,
}

public sealed partial class ReportViewModel : ObservableObject
{
    private readonly IServerStatusService _status;
    private readonly IApiClient _api;
    private readonly LiveTranscriptViewModel _transcript;

    public ReportViewModel(IServerStatusService status, IApiClient api, LiveTranscriptViewModel transcript)
    {
        _status = status;
        _api = api;
        _transcript = transcript;
        _status.StateChanged += (_, _) => OnUi(() =>
        {
            OnPropertyChanged(nameof(GenerateEnabled));
            OnPropertyChanged(nameof(SafetyBanner));
        });
    }

    private static void OnUi(Action action)
    {
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is not null && !dispatcher.CheckAccess())
        {
            dispatcher.BeginInvoke(action);
        }
        else
        {
            action();
        }
    }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanFinalize))]
    private ReportState _state = ReportState.NoDraft;

    [ObservableProperty]
    private string _draftText = "";

    [ObservableProperty]
    private string _templateKey = "general-clinical-note";

    [ObservableProperty]
    private string? _message;

    [ObservableProperty]
    private bool _isGenerating;

    public string SafetyBanner => GenerateEnabled
        ? "Draft content is assistive documentation. Review before finalizing. AI text is never automatically a medical record."
        : "Report generation requires the server Phase 6 capability (feature flag report_generation).";

    public bool GenerateEnabled =>
        _status.State == ConnectionState.Online && _status.Manifest?.Features.ReportGeneration == true;

    public bool CanFinalize => State == ReportState.Draft && !string.IsNullOrWhiteSpace(DraftText);

    [RelayCommand]
    private async Task GenerateAsync()
    {
        if (!GenerateEnabled)
        {
            Message = "Server has not enabled report generation yet.";
            return;
        }
        IsGenerating = true;
        try
        {
            // Phase 6 endpoint: POST /api/v1/reports/draft {transcript, template, language}
            Message = "Report generation endpoint is not wired in this phase.";
        }
        catch (ApiException ex)
        {
            Message = $"Generation failed: {ex.DetailMessage}";
        }
        finally
        {
            IsGenerating = false;
        }
    }

    [RelayCommand]
    private void Finalize()
    {
        if (!CanFinalize)
        {
            Message = "Nothing to finalize.";
            return;
        }
        // Phase 7: PATCH /api/v1/reports/{id}/finalize — explicit clinician act, stored + audited.
        State = ReportState.Finalized;
        Message = "Finalized locally — server persistence and audit land in Phase 7.";
    }

    [RelayCommand]
    private void Approve()
    {
        if (State != ReportState.Finalized)
        {
            Message = "Approve requires a finalized report (explicit two-step).";
            return;
        }
        State = ReportState.Approved;
        Message = "Approved.";
    }

    [RelayCommand]
    private void ReopenDraft()
    {
        if (State == ReportState.Approved)
        {
            Message = "Approved reports are immutable — create an addendum instead (Phase 7).";
            return;
        }
        State = ReportState.Draft;
        Message = "Reopened for editing.";
    }
}
