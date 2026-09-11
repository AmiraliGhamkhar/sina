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

// Reconnect policy: exponential growth, hard cap, attempt budget, reset.
using MedicalScribe.WPF.Services;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class ReconnectPolicyTests
{
    [Fact]
    public void DelaysGrowExponentially()
    {
        var p = new ReconnectPolicy(maxAttempts: 5, initialDelay: TimeSpan.FromSeconds(1));
        Assert.Equal(TimeSpan.FromSeconds(1), p.NextDelay());
        Assert.Equal(TimeSpan.FromSeconds(2), p.NextDelay());
        Assert.Equal(TimeSpan.FromSeconds(4), p.NextDelay());
    }

    [Fact]
    public void DelayIsCappedAtMax()
    {
        var p = new ReconnectPolicy(
            maxAttempts: 10,
            initialDelay: TimeSpan.FromSeconds(4),
            maxDelay: TimeSpan.FromSeconds(30));
        p.NextDelay(); // 4
        p.NextDelay(); // 8
        p.NextDelay(); // 16
        Assert.Equal(TimeSpan.FromSeconds(30), p.NextDelay()); // 32 → capped
        Assert.Equal(TimeSpan.FromSeconds(30), p.NextDelay());
    }

    [Fact]
    public void BudgetExhaustionStopsRetrying()
    {
        var p = new ReconnectPolicy(maxAttempts: 3, initialDelay: TimeSpan.FromMilliseconds(1));
        Assert.True(p.ShouldRetry());
        p.NextDelay();
        p.NextDelay();
        p.NextDelay();
        Assert.Equal(3, p.Attempts);
        Assert.False(p.ShouldRetry());
    }

    [Fact]
    public void SuccessResetsTheCounter()
    {
        var p = new ReconnectPolicy(maxAttempts: 2, initialDelay: TimeSpan.FromMilliseconds(1));
        p.NextDelay();
        p.NextDelay();
        Assert.False(p.ShouldRetry());
        p.Reset();
        Assert.True(p.ShouldRetry());
        Assert.Equal(0, p.Attempts);
    }

    [Fact]
    public void ZeroAttemptsNeverRetries()
    {
        var p = new ReconnectPolicy(maxAttempts: 0);
        Assert.False(p.ShouldRetry());
    }
}
