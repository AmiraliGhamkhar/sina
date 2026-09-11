// Phase 8 — first-run probe URL normalization (pure logic, no WPF deps).
using MedicalScribe.WPF.Infrastructure;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class FirstRunProbeTests
{
    [Theory]
    [InlineData("http://localhost:8000", "http://localhost:8000")]
    [InlineData(" https://scribe.clinic.example/ ", "https://scribe.clinic.example")]
    [InlineData("http://10.0.0.5:8080/", "http://10.0.0.5:8080")]
    public void ValidUrlsNormalizeAndTrim(string raw, string expected)
    {
        Assert.True(FirstRunProbe.TryNormalizeUrl(raw, out var normalized));
        Assert.Equal(expected, normalized);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("localhost:8000")]        // no scheme
    [InlineData("ftp://example.com")]     // wrong scheme
    [InlineData("not a url at all")]
    public void InvalidUrlsAreRejected(string raw)
    {
        Assert.False(FirstRunProbe.TryNormalizeUrl(raw, out _));
    }
}
