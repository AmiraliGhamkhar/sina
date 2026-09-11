/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

// Login state + token storage for the API client.
// The token lives only in process memory: no password vaulting, no provider
// credentials on the client ever (spec §2/§14).
//
// TokenStore is a separate singleton so ApiClient (needs the bearer) and
// AuthorizationService (owns the login flow) don't form a DI cycle.
using CommunityToolkit.Mvvm.ComponentModel;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;

namespace MedicalScribe.WPF.Services;

public interface IAccessTokenSource
{
    string? AccessToken { get; }
    string? RefreshToken { get; }
    void SetTokens(string accessToken, string? refreshToken, int expiresInSeconds);
    void Clear();
}

public sealed class TokenStore : IAccessTokenSource
{
    private DateTime _expiresUtc = DateTime.MinValue;

    public string? AccessToken { get; private set; }

    // Phase 7: one-time refresh token (sent only to /auth/refresh + logout,
    // never persisted to disk)
    public string? RefreshToken { get; private set; }

    /// <summary>Phase 8: when a proactive refresh should fire (expiry minus a
    /// safety margin). MinValue when no tokens are held.</summary>
    public DateTime RefreshDueAtUtc { get; private set; } = DateTime.MinValue;

    public bool IsValid => !string.IsNullOrEmpty(AccessToken) && DateTime.UtcNow < _expiresUtc;

    /// <summary>Fires whenever tokens are set or cleared (login, refresh
    /// rotation, logout) — the refresh scheduler re-arms on this.</summary>
    public event Action? TokensChanged;

    public void SetTokens(string accessToken, string? refreshToken, int expiresInSeconds)
    {
        AccessToken = accessToken;
        RefreshToken = refreshToken;
        // refresh 30 s early so in-flight requests don't race expiry
        _expiresUtc = DateTime.UtcNow.AddSeconds(Math.Max(30, expiresInSeconds - 30));
        // proactive refresh (Phase 8): rotate a minute before expiry, but
        // never sooner than 5 s (short-TTL tokens shouldn't tight-loop)
        RefreshDueAtUtc = DateTime.UtcNow.AddSeconds(Math.Max(5, expiresInSeconds - 60));
        TokensChanged?.Invoke();
    }

    public void Clear()
    {
        AccessToken = null;
        RefreshToken = null;
        _expiresUtc = DateTime.MinValue;
        RefreshDueAtUtc = DateTime.MinValue;
        TokensChanged?.Invoke();
    }
}

public interface IAuthorizationService
{
    bool IsAuthenticated { get; }
    PrincipalDto? CurrentUser { get; }
    event EventHandler? AuthStateChanged;
    Task LoginAsync(string username, string password, CancellationToken ct = default);
    Task LogoutAsync(CancellationToken ct = default);
}

public sealed partial class AuthorizationService : ObservableObject, IAuthorizationService, IDisposable
{
    private readonly IApiClient _api;
    private readonly TokenStore _tokens;
    private readonly ILoggerService _logger;

    public AuthorizationService(IApiClient api, TokenStore tokens, ILoggerService logger)
    {
        _api = api;
        _tokens = tokens;
        _logger = logger;
        // Phase 8 auto-refresh: re-arm the rotation timer on every token
        // change (login, refresh, logout)
        _tokens.TokensChanged += ScheduleRefreshTimer;
    }

    // -- proactive token rotation (Phase 8) -------------------------------------
    // The access JWT lives ~30 min; without rotation a clinician gets logged
    // out mid-shift. One refresh timer, re-armed on TokensChanged, fires
    // RefreshDueAtUtc (expiry − 60 s). One-shot refresh tokens mean the
    // rotation MUST never overlap — hence the _refreshing guard.
    private readonly object _timerLock = new();
    private Timer? _refreshTimer;
    private volatile bool _refreshing;

    private void ScheduleRefreshTimer()
    {
        lock (_timerLock)
        {
            _refreshTimer?.Dispose();
            _refreshTimer = null;
            var refresh = _tokens.RefreshToken;
            if (string.IsNullOrEmpty(refresh) || !_tokens.IsValid)
            {
                return; // logged out (or never logged in) — no timer
            }
            var due = _tokens.RefreshDueAtUtc - DateTime.UtcNow;
            if (due < TimeSpan.Zero)
            {
                due = TimeSpan.Zero;
            }
            _refreshTimer = new Timer(
                _ => _ = RotateTokensAsync(), null, due, Timeout.InfiniteTimeSpan);
        }
    }

    private async Task RotateTokensAsync()
    {
        if (_refreshing)
        {
            return; // never two overlapping rotations (reuse revokes all sessions)
        }
        _refreshing = true;
        try
        {
            var refresh = _tokens.RefreshToken;
            if (string.IsNullOrEmpty(refresh))
            {
                return;
            }
            await _api.RefreshAsync(refresh).ConfigureAwait(false);
            _logger.Info("token refreshed");
            // ApiClient.RefreshAsync stored the new pair → TokensChanged →
            // the timer is already re-armed for the next expiry
        }
        catch (ApiException ex) when (ex.StatusCode == HttpStatusCode.Unauthorized)
        {
            // rotation is dead (revoked / reused / expired): back to login
            _logger.Warn($"refresh rejected ({ex.Code}) — session ended");
            _tokens.Clear();
            AuthStateChanged?.Invoke(this, EventArgs.Empty);
        }
        catch (Exception ex)
        {
            // transient (offline, 5xx, rate limit): retry in 30 s; a network
            // blip must never log a clinician out
            _logger.Warn($"refresh failed ({ex.Message}) — retrying in 30 s");
            lock (_timerLock)
            {
                _refreshTimer?.Dispose();
                _refreshTimer = new Timer(
                    _ => _ = RotateTokensAsync(), null, TimeSpan.FromSeconds(30),
                    Timeout.InfiniteTimeSpan);
            }
        }
        finally
        {
            _refreshing = false;
        }
    }

    public bool IsAuthenticated => _tokens.IsValid;

    [ObservableProperty]
    private PrincipalDto? _currentUser;

    public event EventHandler? AuthStateChanged;

    public async Task LoginAsync(string username, string password, CancellationToken ct = default)
    {
        // ApiClient stores the access token via IAccessTokenSource on success.
        await _api.LoginAsync(new LoginRequestDto(username, password), ct);
        try
        {
            CurrentUser = await _api.GetCurrentUserAsync(ct);
        }
        catch (ApiException)
        {
            // token accepted but /me failed — stay logged in; UI shows "unknown role"
            _logger.Warn("login succeeded but principal lookup failed");
            CurrentUser = null;
        }
        _logger.Info($"login ok user={username}");
        AuthStateChanged?.Invoke(this, EventArgs.Empty);
    }

    public async Task LogoutAsync(CancellationToken ct = default)
    {
        try
        {
            if (_tokens.IsValid)
            {
                await _api.LogoutAsync(ct);
            }
        }
        catch (ApiException ex)
        {
            if (ex.Code is not ("AUTH_NOT_IMPLEMENTED" or "NOT_FOUND"))
            {
                _logger.Warn($"logout call failed: {ex.Code}");
            }
        }
        finally
        {
            _tokens.Clear();
            CurrentUser = null;
            AuthStateChanged?.Invoke(this, EventArgs.Empty);
        }
    }
    public void Dispose()
    {
        _tokens.TokensChanged -= ScheduleRefreshTimer;
        lock (_timerLock)
        {
            _refreshTimer?.Dispose();
            _refreshTimer = null;
        }
    }

}
