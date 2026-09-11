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

// Error model mirroring the backend envelope:
//   {"error":{"code":"AUTH_NOT_IMPLEMENTED","message":"..."}}
// ViewModels switch on Code (stable), display Message.
using System.Net;

namespace MedicalScribe.WPF.Infrastructure;

public sealed class ApiException : Exception
{
    public ApiException(
        HttpStatusCode statusCode,
        string code,
        string message,
        JsonElement? details = null)
        : base($"{code}: {message}")
    {
        StatusCode = statusCode;
        Code = code;
        DetailMessage = message;
        Details = details;
    }

    public HttpStatusCode StatusCode { get; }

    /// <summary>Stable machine-readable code (see backend/api/errors.py).</summary>
    public string Code { get; }

    public string DetailMessage { get; }

    public JsonElement? Details { get; }

    public bool IsNetworkError => StatusCode == 0;

    public static ApiException Network(string message) =>
        new((HttpStatusCode)0, "NETWORK", message);
}
