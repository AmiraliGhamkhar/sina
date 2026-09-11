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

// WsFrameParser unit tests — every server frame type from
// docs/WEBSOCKET_PROTOCOL.md, plus forward-compat and malformed inputs.
using MedicalScribe.WPF.Services;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class WsFrameParserTests
{
    [Fact]
    public void ParsesSessionStarted()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"session.started","session_id":"ws_abc","protocol":1,"provider":"mock","mode":"auto","language":"fa-en","server_time_ms":1}""");
        var started = Assert.IsType<WsStartedEvent>(evt);
        Assert.Equal("ws_abc", started.SessionId);
        Assert.Equal("mock", started.Provider);
    }

    [Fact]
    public void ParsesGrowingInterimWithStableSegmentId()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"transcript.interim","session_id":"ws_abc","text":"بیمار با","segment_id":"seg_0000","start_ms":20}""");
        var interim = Assert.IsType<WsInterimEvent>(evt);
        Assert.Equal("بیمار با", interim.Text);
        Assert.Equal("seg_0000", interim.SegmentId);
        Assert.Equal(20, interim.StartMs);
    }

    [Fact]
    public void ParsesFinalWithConfidenceAndLanguage()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"transcript.final","session_id":"ws_abc","text":"ECG بدون تغییر است.","segment_id":"seg_0000","start_ms":0,"end_ms":1840,"language":"fa-en","confidence":0.91}""");
        var final = Assert.IsType<WsFinalEvent>(evt);
        Assert.Equal(1840, final.EndMs);
        Assert.Equal(0.91, final.Confidence);
    }

    [Fact]
    public void ParsesFinalWithoutConfidence()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"transcript.final","session_id":"ws_abc","text":"x","segment_id":"s","start_ms":0,"end_ms":1}""");
        var final = Assert.IsType<WsFinalEvent>(evt);
        Assert.Null(final.Confidence);
        Assert.Null(final.Language);
    }

    [Fact]
    public void ParsesWarning()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"warning","session_id":"ws_abc","code":"AUDIO_DROPPED_PAUSED","message":"paused; buffer overflow","segment_id":null}""");
        var warning = Assert.IsType<WsWarningEvent>(evt);
        Assert.Equal("AUDIO_DROPPED_PAUSED", warning.Code);
        Assert.Null(warning.SegmentId);
    }

    [Fact]
    public void ParsesRecoverableAndFatalErrors()
    {
        var soft = Assert.IsType<WsErrorEvent>(WsFrameParser.Parse(
            """{"v":1,"type":"error","session_id":"ws_abc","code":"PROVIDER_UNAVAILABLE","message":"boom","recoverable":true}"""));
        Assert.True(soft.Recoverable);
        var hard = Assert.IsType<WsErrorEvent>(WsFrameParser.Parse(
            """{"v":1,"type":"error","session_id":"ws_abc","code":"PROTOCOL_VERSION","message":"no","recoverable":false}"""));
        Assert.False(hard.Recoverable);
    }

    [Fact]
    public void ParsesCommandDetectedWithDictArgs()
    {
        // protocol v1: the frame type is "command.detected"; args is a dict
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"command.detected","session_id":"ws_abc","command":"insert_section","args":{"section_title":"طرح درمان"},"utterance_text":"درج بخش طرح درمان","segment_id":"seg_0002"}""");
        var cmd = Assert.IsType<WsCommandEvent>(evt);
        Assert.Equal("insert_section", cmd.Command);
        Assert.Equal("طرح درمان", cmd.Args["section_title"]);
        Assert.Equal("درج بخش طرح درمان", cmd.UtteranceText);
        Assert.Equal("seg_0002", cmd.SegmentId);
    }

    [Fact]
    public void ParsesCommandDetectedWithoutArgs()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"command.detected","command":"new_paragraph","args":{}}""");
        var cmd = Assert.IsType<WsCommandEvent>(evt);
        Assert.Empty(cmd.Args);
        Assert.Equal("", cmd.UtteranceText);
    }

    [Fact]
    public void ParsesCompletedCounts()
    {
        var evt = WsFrameParser.Parse(
            """{"v":1,"type":"session.completed","session_id":"ws_abc","segment_count":12,"duration_ms":45000,"provider":"mock"}""");
        var done = Assert.IsType<WsCompletedEvent>(evt);
        Assert.Equal(12, done.SegmentCount);
        Assert.Equal(45000, done.DurationMs);
    }

    [Fact]
    public void UnknownTypeIsTolerated()
    {
        // v1 is additive: future frames must not break current clients
        var evt = WsFrameParser.Parse("""{"v":1,"type":"transcript.correction","session_id":"ws_abc"}""");
        var unknown = Assert.IsType<WsUnknownEvent>(evt);
        Assert.Equal("transcript.correction", unknown.UnknownType);
    }

    [Theory]
    [InlineData("")]
    [InlineData("{not json")]
    [InlineData("[1,2,3]")]
    [InlineData("null")]
    public void MalformedFramesReturnNullWithoutThrowing(string json) =>
        Assert.Null(WsFrameParser.Parse(json));
}
