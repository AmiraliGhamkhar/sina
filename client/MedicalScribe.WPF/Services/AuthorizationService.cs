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
    void SetAccessToken(string token, int expiresInSeconds);
    void Clear();
}

public sealed class TokenStore : IAccessTokenSource
{
    private DateTime _expiresUtc = DateTime.MinValue;

    public string? AccessToken { get; private set; }

    public bool IsValid => !string.IsNullOrEmpty(AccessToken) && DateTime.UtcNow < _expiresUtc;

    public void SetAccessToken(string token, int expiresInSeconds)
    {
        AccessToken = token;
        // refresh 30 s early so in-flight requests don't race expiry
        _expiresUtc = DateTime.UtcNow.AddSeconds(Math.Max(30, expiresInSeconds - 30));
    }

    public void Clear()
    {
        AccessToken = null;
        _expiresUtc = DateTime.MinValue;
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

public sealed partial class AuthorizationService : ObservableObject, IAuthorizationService
{
    private readonly IApiClient _api;
    private readonly TokenStore _tokens;
    private readonly ILoggerService _logger;

    public AuthorizationService(IApiClient api, TokenStore tokens, ILoggerService logger)
    {
        _api = api;
        _tokens = tokens;
        _logger = logger;
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
}
