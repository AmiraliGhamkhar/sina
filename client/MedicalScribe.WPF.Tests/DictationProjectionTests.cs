// DictationSession.ProjectEvent — the pure protocol→view-model mapping, and
// LiveTranscriptViewModel's sink behavior. Headless: ObservableCollection +
// CommunityToolkit work without a WPF Application for these code paths.
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;
using MedicalScribe.WPF.ViewModels;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class DictationProjectionTests
{
    [Fact]
    public void InterimProjectsToGrowingPreview()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsInterimEvent("بیمار با درد", "seg_0000", 0) { SessionId = "ws_1" }, vm);
        Assert.Equal("بیمار با درد", vm.InterimText);
        Assert.True(vm.IsListening);
        Assert.Empty(vm.Segments);
    }

    [Fact]
    public void FinalAppendsSegmentAndClearsInterim()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsInterimEvent("x", "seg_0000", 0), vm);
        DictationSession.ProjectEvent(
            new WsFinalEvent("ECG نرمال است.", "seg_0000", 0, 1200, "fa-en", 0.9), vm);
        Assert.Equal("", vm.InterimText);
        var seg = Assert.Single(vm.Segments);
        Assert.Equal("ECG نرمال است.", seg.Text);
        Assert.Equal("seg_0000", seg.SegmentId);
        Assert.Equal(SegmentOrigin.Dictated, seg.Origin);
        Assert.Equal("00:00 – 00:01", seg.Timestamp);
    }

    [Fact]
    public void WarningSurfacesInCollection()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsWarningEvent("AUDIO_DROPPED_PAUSED", "buffer overflow while paused", null), vm);
        var w = Assert.Single(vm.Warnings);
        Assert.Equal("AUDIO_DROPPED_PAUSED", w.Code);
    }

    [Fact]
    public void ErrorSetsStatusNoteNotTranscript()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsErrorEvent("PROVIDER_UNAVAILABLE", "mock is down", true, null), vm);
        Assert.Contains("PROVIDER_UNAVAILABLE", vm.StatusNote);
        Assert.Empty(vm.Segments);
    }

    [Fact]
    public void CompletedFrameReportsCounts()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsFinalEvent("a", "s1", 0, 10, null, null), vm);
        DictationSession.ProjectEvent(new WsCompletedEvent(1, 1500, "mock"), vm);
        Assert.Contains("1 segments", vm.StatusNote);
        Assert.Equal("a", vm.FullText);
    }

    [Fact]
    public void FullTextJoinsSegmentsWithBlankLines()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsFinalEvent("الف", "s1", 0, 10, null, null), vm);
        DictationSession.ProjectEvent(new WsFinalEvent("ب", "s2", 10, 20, null, null), vm);
        Assert.Equal("الف\n\nب", vm.FullText);
    }

    [Fact]
    public void DeleteLastRemovesNewestSegment()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsFinalEvent("یک", "s1", 0, 10, null, null), vm);
        DictationSession.ProjectEvent(new WsFinalEvent("دو", "s2", 10, 20, null, null), vm);
        vm.DeleteLastSentenceCommand.Execute(null);
        Assert.Single(vm.Segments);
        Assert.Equal("یک", vm.Segments[0].Text);
    }

    [Fact]
    public void EditedSegmentIsFlaggedForAudit()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsFinalEvent("دوز 40 mg", "s1", 0, 10, null, null), vm);
        vm.SelectedSegment = vm.Segments[0];
        vm.SelectedSegment.Text = "دوز 400 mg";
        vm.CommitSelectedTextEditCommand.Execute(null);
        Assert.True(vm.Segments[0].IsModified);
        Assert.Equal("دوز 400 mg", vm.FullText);
    }
}
