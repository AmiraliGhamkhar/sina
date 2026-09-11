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

// HttpClient-based REST transport. Uses IAccessTokenSource so it never owns
// credentials logic (composition, no service locator).
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.Infrastructure;

public sealed class ApiClient : IApiClient
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly HttpClient _http;
    private readonly IAccessTokenSource _tokens;
    private readonly ILoggerService _logger;

    public ApiClient(HttpClient http, IAccessTokenSource tokens, ILoggerService logger)
    {
        _http = http;
        _tokens = tokens;
        _logger = logger;
        _http.Timeout = TimeSpan.FromSeconds(20);
    }

    public Uri BaseAddress => _http.BaseAddress ?? new Uri("http://localhost:8000");

    // -- endpoints -----------------------------------------------------------

    public Task<HealthDto?> GetHealthAsync(CancellationToken ct = default) =>
        GetAsync<HealthDto>("/health", ct);

    public Task<VersionDto?> GetVersionAsync(CancellationToken ct = default) =>
        GetAsync<VersionDto>("/api/v1/version", ct);

    public Task<ClientManifestDto?> GetManifestAsync(CancellationToken ct = default) =>
        GetAsync<ClientManifestDto>("/api/v1/config/manifest", ct);

    public async Task<IReadOnlyList<ProviderInfoDto>> GetProvidersAsync(
        bool probeHealth = false, CancellationToken ct = default)
    {
        var path = probeHealth ? "/api/v1/providers?probe=health" : "/api/v1/providers";
        var list = await GetAsync<List<ProviderInfoDto>>(path, ct);
        return list ?? new List<ProviderInfoDto>();
    }

    public async Task<TokenPairDto> LoginAsync(LoginRequestDto request, CancellationToken ct = default)
    {
        var result = await PostAsync<TokenPairDto>("/api/v1/auth/login", request, ct);
        _tokens.SetTokens(result.AccessToken, result.RefreshToken, result.ExpiresIn);
        return result;
    }

    public async Task<TokenPairDto> RefreshAsync(string refreshToken, CancellationToken ct = default)
    {
        var result = await PostAsync<TokenPairDto>(
            "/api/v1/auth/refresh", new { refresh_token = refreshToken }, ct);
        _tokens.SetTokens(result.AccessToken, result.RefreshToken, result.ExpiresIn);
        return result;
    }

    public async Task LogoutAsync(CancellationToken ct = default)
    {
        try
        {
            // revoke the one-time refresh token server-side (Phase 7 rotation);
            // the server tolerates an absent token
            await PostAsync<object>(
                "/api/v1/auth/logout",
                new { refresh_token = _tokens.RefreshToken },
                ct);
        }
        finally
        {
            _tokens.Clear();
        }
    }

    public Task<PrincipalDto?> GetCurrentUserAsync(CancellationToken ct = default) =>
        GetAsync<PrincipalDto>("/api/v1/auth/me", ct);

    // -- Phase 6: templates + report lifecycle -----------------------------------

    public async Task<IReadOnlyList<ReportTemplateDto>> GetReportTemplatesAsync(
        CancellationToken ct = default)
    {
        var list = await GetAsync<TemplateListDto>("/api/v1/report-templates", ct);
        return list?.Templates ?? new List<ReportTemplateDto>();
    }

    public async Task<string> ForkTemplateAsync(
        string sourceKey, string newKey, string? newName, CancellationToken ct = default)
    {
        var forked = await PostAsync<ReportTemplateDto>(
            $"/api/v1/report-templates/{sourceKey}/fork",
            new { new_key = newKey, new_name = newName }, ct);
        return forked.Key;
    }

    public Task<ReportDraftResponseDto> CreateReportDraftAsync(
        ReportDraftRequestDto request, string encounterId, CancellationToken ct = default) =>
        PostAsync<ReportDraftResponseDto>($"/api/v1/reports/{encounterId}/draft", request, ct);

    public Task<ReportDto?> GetReportAsync(string reportId, CancellationToken ct = default) =>
        GetAsync<ReportDto>($"/api/v1/reports/{reportId}", ct);

    public Task<ReportDto> PatchReportSectionsAsync(
        string reportId, IReadOnlyDictionary<string, string> sections, CancellationToken ct = default) =>
        PatchAsync<ReportDto>($"/api/v1/reports/{reportId}", new { sections }, ct);

    public Task<ReportDto> AcknowledgeWarningAsync(
        string reportId, string warningId, string justification, CancellationToken ct = default) =>
        PostAsync<ReportDto>($"/api/v1/reports/{reportId}/acknowledge",
            new { warning_id = warningId, justification }, ct);

    public Task<ReportDto> FinalizeReportAsync(string reportId, CancellationToken ct = default) =>
        PostAsync<ReportDto>($"/api/v1/reports/{reportId}/finalize", new { }, ct);

    public Task<ReportDto> ReopenReportAsync(string reportId, CancellationToken ct = default) =>
        PostAsync<ReportDto>($"/api/v1/reports/{reportId}/reopen", new { }, ct);

    public Task<ReportDto> ApproveReportAsync(string reportId, CancellationToken ct = default) =>
        PostAsync<ReportDto>($"/api/v1/reports/{reportId}/approve", new { }, ct);

    public Task<ReportDto> AmendReportAsync(string reportId, CancellationToken ct = default) =>
        PostAsync<ReportDto>($"/api/v1/reports/{reportId}/amend", new { }, ct);

    public Task<NormalizationResultDto?> NormalizeTextAsync(
        string text, CancellationToken ct = default) =>
        PostAsyncOrNullAsync<NormalizationResultDto>(
            "/api/v1/terminology/normalize", new { text }, ct);

    // -- transport plumbing ----------------------------------------------------

    private async Task<T?> GetAsync<T>(string path, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Get, path, content: null, ct);
        return await ReadJsonAsync<T>(response, ct);
    }

    private async Task<T> PatchAsync<T>(string path, object body, CancellationToken ct)
    {
        using var response = await SendAsync(
            HttpMethod.Patch, path, JsonContent.Create(body, options: JsonOptions), ct);
        var result = await ReadJsonAsync<T>(response, ct);
        return result ?? throw new ApiException(
            response.StatusCode, "EMPTY_RESPONSE", "server returned no body");
    }

    private async Task<T?> PostAsyncOrNullAsync<T>(string path, object body, CancellationToken ct)
    {
        using var response = await SendAsync(
            HttpMethod.Post, path, JsonContent.Create(body, options: JsonOptions), ct);
        return await ReadJsonAsync<T>(response, ct);
    }

    private async Task<T> PostAsync<T>(string path, object body, CancellationToken ct)
    {
        using var response = await SendAsync(
            HttpMethod.Post, path, JsonContent.Create(body, options: JsonOptions), ct);
        var result = await ReadJsonAsync<T>(response, ct);
        return result ?? throw new ApiException(
            response.StatusCode, "EMPTY_RESPONSE", "server returned no body");
    }

    private async Task<HttpResponseMessage> SendAsync(
        HttpMethod method, string path, HttpContent? content, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(method, path);
        if (content is not null)
        {
            request.Content = content;
        }
        var token = _tokens.AccessToken;
        if (!string.IsNullOrEmpty(token))
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, ct);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw ApiException.Network("server timeout — check that the API is running and reachable");
        }
        catch (HttpRequestException ex)
        {
            _logger.Error($"request to {path} failed: {ex.Message}");
            throw ApiException.Network($"cannot reach {BaseAuthoritative()} — {ex.Message}");
        }

        if (!response.IsSuccessStatusCode)
        {
            throw await ToApiExceptionAsync(response, ct);
        }
        return response;
    }

    private string BaseAuthoritative() => _http.BaseAddress?.ToString() ?? "(unset)";

    private async Task<ApiException> ToApiExceptionAsync(HttpResponseMessage response, CancellationToken ct)
    {
        var text = await response.Content.ReadAsStringAsync(ct);
        string code = response.StatusCode switch
        {
            HttpStatusCode.Unauthorized => "UNAUTHENTICATED",
            HttpStatusCode.Forbidden => "FORBIDDEN",
            HttpStatusCode.NotFound => "NOT_FOUND",
            HttpStatusCode.TooManyRequests => "RATE_LIMITED",
            _ => "HTTP_ERROR",
        };
        string message = text.Length > 300 ? text[..300] : text;
        try
        {
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("error", out var error))
            {
                if (error.TryGetProperty("code", out var c)) code = c.GetString() ?? code;
                if (error.TryGetProperty("message", out var m)) message = m.GetString() ?? message;
                if (error.TryGetProperty("details", out var d))
                {
                    return new ApiException(response.StatusCode, code, message, d.Clone());
                }
            }
        }
        catch (JsonException)
        {
            // non-JSON error body (proxy failure etc.) — keep raw text
        }
        return new ApiException(response.StatusCode, code, message);
    }

    private static async Task<T?> ReadJsonAsync<T>(HttpResponseMessage response, CancellationToken ct)
    {
        var stream = await response.Content.ReadAsStreamAsync(ct);
        if (stream.CanSeek && stream.Length == 0)
        {
            return default;
        }
        try
        {
            return await JsonSerializer.DeserializeAsync<T>(stream, JsonOptions, ct);
        }
        catch (JsonException ex)
        {
            throw new ApiException(response.StatusCode, "BAD_JSON", $"malformed server response: {ex.Message}");
        }
    }
}
