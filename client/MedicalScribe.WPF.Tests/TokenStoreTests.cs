// Phase 8 — token store rotation semantics (pure logic, no WPF deps).
using MedicalScribe.WPF.Services;
using Xunit;

namespace MedicalScribe.WPF.Tests;

public class TokenStoreTests
{
    [Fact]
    public void SetTokensRaisesChangedAndSchedulesRefreshBeforeExpiry()
    {
        var store = new TokenStore();
        var raised = 0;
        store.TokensChanged += () => raised++;

        store.SetTokens("access-1", "refresh-1", expiresInSeconds: 1800);

        Assert.Equal(1, raised);
        Assert.True(store.IsValid);
        Assert.Equal("access-1", store.AccessToken);
        Assert.Equal("refresh-1", store.RefreshToken);
        // refresh due ≈ expiry − 60 s (allow scheduling slack)
        var dueInSeconds = (store.RefreshDueAtUtc - DateTime.UtcNow).TotalSeconds;
        Assert.InRange(dueInSeconds, 1700, 1750);
    }

    [Fact]
    public void ShortTtlClampsRefreshDueToAtLeastFiveSeconds()
    {
        var store = new TokenStore();
        store.SetTokens("a", "r", expiresInSeconds: 30);
        var dueInSeconds = (store.RefreshDueAtUtc - DateTime.UtcNow).TotalSeconds;
        Assert.InRange(dueInSeconds, 0, 6);
    }

    [Fact]
    public void ClearRaisesChangedAndInvalidates()
    {
        var store = new TokenStore();
        store.SetTokens("a", "r", 1800);
        var raised = 0;
        store.TokensChanged += () => raised++;

        store.Clear();

        Assert.Equal(1, raised);
        Assert.False(store.IsValid);
        Assert.Null(store.AccessToken);
        Assert.Null(store.RefreshToken);
        Assert.Equal(DateTime.MinValue, store.RefreshDueAtUtc);
    }

    [Fact]
    public void RefreshRotationReplacesTokensAndReRaises()
    {
        // one-shot refresh tokens: the new pair replaces the old atomically
        var store = new TokenStore();
        store.SetTokens("access-1", "refresh-1", 1800);
        var raised = 0;
        store.TokensChanged += () => raised++;

        store.SetTokens("access-2", "refresh-2", 1800);

        Assert.Equal(1, raised);
        Assert.Equal("access-2", store.AccessToken);
        Assert.Equal("refresh-2", store.RefreshToken);
        Assert.NotEqual("refresh-1", store.RefreshToken);
    }
}
