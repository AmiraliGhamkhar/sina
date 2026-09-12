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
        // the VM joins with Environment.NewLine (correct for a WPF TextBox);
        // the test must assert the same contract on every OS, not \n literals
        Assert.Equal("الف" + Environment.NewLine + Environment.NewLine + "ب", vm.FullText);
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
    public void StartedFrameSetsSessionId()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsStartedEvent("mock", "auto", "fa-en") { SessionId = "ws_9" }, vm);
        Assert.Equal("ws_9", vm.SessionId);
    }

    [Fact]
    public void StartedFrameNamesTheProviderTheServerActuallyChose()
    {
        // Pinning a provider is a request: the server may substitute a local
        // engine for a private encounter. The clinician has to see which one
        // transcribed their patient, so the status note names it.
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsStartedEvent("9router", "local", "fa-en") { SessionId = "ws_9" }, vm);
        Assert.Contains("9router", vm.StatusNote);
        Assert.Contains("local", vm.StatusNote);
    }

    [Fact]
    public void StartedFrameWithoutProviderLeavesTheStatusNoteAlone()
    {
        var vm = new LiveTranscriptViewModel();
        vm.SetStatusNote("connecting transcription stream…");
        DictationSession.ProjectEvent(
            new WsStartedEvent("", "", "fa-en") { SessionId = "ws_9" }, vm);
        Assert.Equal("connecting transcription stream…", vm.StatusNote);
    }

    [Fact]
    public void NewParagraphCommandInsertsMarkerEntry()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(new WsFinalEvent("جمله اول.", "s1", 0, 10, null, null), vm);
        DictationSession.ProjectEvent(
            new WsCommandEvent("new_paragraph", new Dictionary<string, string>(), "پاراگراف جدید", null),
            vm);
        Assert.Equal(2, vm.Segments.Count);
        Assert.Equal(SegmentOrigin.Command, vm.Segments[1].Origin);
    }

    [Fact]
    public void InsertSectionCommandInsertsHeadingMarker()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsCommandEvent("insert_section",
                new Dictionary<string, string> { ["section_title"] = "طرح درمان" },
                "درج بخش طرح درمان", "seg_2"),
            vm);
        var marker = Assert.Single(vm.Segments);
        Assert.Equal(SegmentOrigin.Command, marker.Origin);
        Assert.Contains("طرح درمان", marker.Text);
    }

    [Fact]
    public void DeleteLastSentenceCommandMirrorsServerEffect()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsFinalEvent("جمله یک. جمله دو.", "s1", 0, 10, null, null), vm);
        DictationSession.ProjectEvent(
            new WsCommandEvent("delete_last_sentence", new Dictionary<string, string>(),
                "حذف جمله آخر", null),
            vm);
        var seg = Assert.Single(vm.Segments);
        Assert.Equal("جمله یک.", seg.Text);
    }

    [Fact]
    public void PauseCommandSetsStatusNotSegments()
    {
        var vm = new LiveTranscriptViewModel();
        DictationSession.ProjectEvent(
            new WsCommandEvent("pause_recording", new Dictionary<string, string>(),
                "توقف ضبط", null),
            vm);
        Assert.Contains("paused", vm.StatusNote);
        Assert.Empty(vm.Segments);
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
