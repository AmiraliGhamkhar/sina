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

// First-run connectivity probe (Phase 8 first-run wizard). UI-free and
// unit-testable: given a candidate server URL, verify it is a reachable
// MedicalScribe API (GET /health) — never any credentials, never a login.
namespace MedicalScribe.WPF.Infrastructure;

public static class FirstRunProbe
{
    public sealed record ProbeResult(bool Ok, string Detail, string NormalizedUrl);

    public static bool TryNormalizeUrl(string? raw, out string normalized)
    {
        normalized = "";
        if (string.IsNullOrWhiteSpace(raw))
        {
            return false;
        }
        if (!Uri.TryCreate(raw.Trim(), UriKind.Absolute, out var uri) ||
            (uri.Scheme != "http" && uri.Scheme != "https"))
        {
            return false;
        }
        normalized = uri.ToString().TrimEnd('/');
        return true;
    }

    public static async Task<ProbeResult> TryConnectAsync(string? rawUrl, CancellationToken ct = default)
    {
        if (!TryNormalizeUrl(rawUrl, out var url))
        {
            return new ProbeResult(false, "Enter a full URL (http:// or https://…)", url);
        }
        try
        {
            using var http = new HttpClient { BaseAddress = new Uri(url), Timeout = TimeSpan.FromSeconds(5) };
            using var resp = await http.GetAsync("/health", ct).ConfigureAwait(false);
            if (!resp.IsSuccessStatusCode)
            {
                return new ProbeResult(false, $"Server answered HTTP {(int)resp.StatusCode}", url);
            }
            var body = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            using var doc = JsonDocument.Parse(body);
            var name = doc.RootElement.TryGetProperty("name", out var n) ? n.GetString() : null;
            // any JSON /health that identifies itself as MedicalScribe counts
            var isOurs = name is null || name.Contains("MedicalScribe", StringComparison.OrdinalIgnoreCase);
            return isOurs
                ? new ProbeResult(true, "Connected — MedicalScribe API reachable", url)
                : new ProbeResult(false, "Another service is listening there", url);
        }
        catch (Exception ex)
        {
            return new ProbeResult(false, $"No answer: {ex.Message}", url);
        }
    }
}
