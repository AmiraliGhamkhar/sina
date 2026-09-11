// Live transcription WebSocket client (Phase 2). Responsibilities:
//  • connect wss/ws to the backend WS endpoint (path + policy from manifest)
//  • send session.start, binary audio.chunk frames (raw PCM16 — cheaper than
//    base64; the backend accepts both, docs/WEBSOCKET_PROTOCOL.md)
//  • parse frames into WsEvent, expose them as events
//  • drop-oldest bounded send queue so a stalled socket never blocks capture
//  • bounded reconnect with exponential backoff (ReconnectPolicy)
// All UI-thread marshaling happens in DictationSession, not here.
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Models;
using MedicalScribe.WPF.Settings;

namespace MedicalScribe.WPF.Services;

public enum StreamState
{
    Disconnected,
    Connecting,
    Connected,
    Reconnecting,
    Faulted,
}

public interface ITranscriptionStream : IAsyncDisposable
{
    StreamState State { get; }
    string? SessionId { get; }
    long DroppedAudioFrames { get; }

    event EventHandler<StreamState>? StateChanged;
    event EventHandler<WsEvent>? EventReceived;

    /// <summary>Raised when session.completed arrives (graceful end).</summary>
    event EventHandler<WsCompletedEvent>? Completed;

    Task<bool> StartSessionAsync(
        string language, string routingMode, string? provider, bool privacyRequired,
        CancellationToken ct = default);
    ValueTask SendAudioAsync(byte[] pcm16, CancellationToken ct = default);
    Task PauseAsync(CancellationToken ct = default);
    Task ResumeAsync(CancellationToken ct = default);

    /// <summary>Send session.stop and await session.completed (with a cap).</summary>
    Task<bool> StopSessionAsync(TimeSpan timeout, CancellationToken ct = default);
}

public sealed class WsTranscriptionClient : ITranscriptionStream
{
    public const int SendQueueFrames = 16; // ~1.6 s of 100 ms chunks max queued
    private static readonly TimeSpan HandshakeTimeout = TimeSpan.FromSeconds(5);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    private readonly IServerStatusService _status;
    private readonly IAccessTokenSource _tokens;
    private readonly ILoggerService _log;
    private readonly AppSettings _settings;
    private readonly ReconnectPolicy _reconnect;
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private readonly Channel<byte[]> _audio =
        Channel.CreateBounded<byte[]>(new BoundedChannelOptions(SendQueueFrames)
        {
            FullMode = BoundedChannelFullMode.DropWrite, // keep newest frames, count drops
            SingleReader = true,
            SingleWriter = false,
        });

    private ClientWebSocket? _socket;
    private CancellationTokenSource? _loopCts;
    private Task? _receiveLoop;
    private Task? _sendLoop;
    private TaskCompletionSource<WsCompletedEvent>? _completedTcs;
    private TaskCompletionSource<string>? _startedTcs;
    private long _dropped;
    private volatile bool _sessionWanted;
    private string _startLanguage = "fa-en";
    private string _startMode = "auto";
    private string? _startProvider;
    private bool _startPrivacy;

    public WsTranscriptionClient(
        IServerStatusService status,
        IAccessTokenSource tokens,
        ILoggerService log,
        AppSettings settings,
        ReconnectPolicy? reconnect = null)
    {
        _status = status;
        _tokens = tokens;
        _log = log;
        _settings = settings;
        _reconnect = reconnect ?? new ReconnectPolicy(maxAttempts: settings.ReconnectMaxAttempts);
    }

    public StreamState State { get; private set; } = StreamState.Disconnected;
    public string? SessionId { get; private set; }
    public long DroppedAudioFrames => Interlocked.Read(ref _dropped);

    public event EventHandler<StreamState>? StateChanged;
    public event EventHandler<WsEvent>? EventReceived;
    public event EventHandler<WsCompletedEvent>? Completed;

    // -- session lifecycle -------------------------------------------------------

    public async Task<bool> StartSessionAsync(
        string language, string routingMode, string? provider, bool privacyRequired,
        CancellationToken ct = default)
    {
        _startLanguage = language;
        _startMode = routingMode;
        _startProvider = provider;
        _startPrivacy = privacyRequired;
        _sessionWanted = true;
        _reconnect.Reset();

        if (!BuildUri(out var uri, out var error))
        {
            _sessionWanted = false;
            Fault(error);
            return false;
        }
        try
        {
            SetState(StreamState.Connecting);
            await ConnectAndHandshakeAsync(uri, ct).ConfigureAwait(false);
            return State == StreamState.Connected;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _log.Error("ws connect failed", ex);
            return await TryReconnectAsync(CancellationToken.None).ConfigureAwait(false);
        }
    }

    private async Task<bool> ConnectAndHandshakeAsync(Uri uri, CancellationToken ct)
    {
        var socket = new ClientWebSocket();
        var token = _tokens.AccessToken;
        if (!string.IsNullOrEmpty(token))
        {
            socket.Options.SetRequestHeader("Authorization", $"Bearer {token}");
        }
        try
        {
            await socket.ConnectAsync(uri, ct).ConfigureAwait(false);
        }
        catch
        {
            socket.Dispose();
            throw;
        }

        // a fresh session must never replay audio left queued by the last one
        while (_audio.Reader.TryRead(out _))
        {
        }

        _socket = socket;
        _loopCts = new CancellationTokenSource();
        var startedTcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        _startedTcs = startedTcs;
        _completedTcs = new TaskCompletionSource<WsCompletedEvent>(TaskCreationOptions.RunContinuationsAsynchronously);
        _receiveLoop = Task.Run(() => ReceiveLoopAsync(socket, _loopCts.Token));
        _sendLoop = Task.Run(() => AudioSendLoopAsync(socket, _loopCts.Token));

        string started;
        try
        {
            await SendControlFrameAsync(BuildStartFrame(), ct).ConfigureAwait(false);
            started = await startedTcs.Task.WaitAsync(HandshakeTimeout, ct).ConfigureAwait(false);
        }
        catch (Exception)
        {
            CleanupSocket(); // handshake died — never leak a half-open socket + loops
            throw;
        }
        SessionId = started;
        SetState(StreamState.Connected);
        _reconnect.Reset();
        _log.Info($"transcription stream connected, session {started}");
        return true;
    }

    private string BuildStartFrame() =>
        JsonSerializer.Serialize(
            new WsStartPayload(
                1,
                "session.start",
                _startLanguage,
                _startMode,
                string.IsNullOrWhiteSpace(_startProvider) ? null : _startProvider,
                _startPrivacy ? true : null), // omit false: server default applies
            JsonOptions);

    public ValueTask SendAudioAsync(byte[] pcm16, CancellationToken ct = default)
    {
        // never block the capture thread: bounded channel; when full, drop the
        // NEW frame (backpressure-safe) and count it for the UI/reconnect note
        if (_audio.Reader.CanCount && _audio.Reader.Count >= SendQueueFrames)
        {
            Interlocked.Increment(ref _dropped);
            return default;
        }
        _audio.Writer.TryWrite(pcm16);
        return default;
    }

    public Task PauseAsync(CancellationToken ct = default) =>
        SendControlFrameAsync("""{"v":1,"type":"session.pause"}""", ct);

    public Task ResumeAsync(CancellationToken ct = default) =>
        SendControlFrameAsync("""{"v":1,"type":"session.resume"}""", ct);

    public async Task<bool> StopSessionAsync(TimeSpan timeout, CancellationToken ct = default)
    {
        _sessionWanted = false;
        var socket = _socket;
        if (socket is null || socket.State != WebSocketState.Open)
        {
            return false;
        }
        try
        {
            await SendControlFrameAsync("""{"v":1,"type":"session.stop"}""", ct).ConfigureAwait(false);
            var done = await _completedTcs!.Task.WaitAsync(timeout, ct).ConfigureAwait(false);
            await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "client stop", ct).ConfigureAwait(false);
            _log.Info($"session completed: {done.SegmentCount} segments, {done.DurationMs} ms");
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _log.Warn($"graceful stop failed ({ex.GetType().Name}); closing hard");
            try
            {
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None)
                    .ConfigureAwait(false);
            }
            catch
            {
                // close races receive-loop fault; already gone
            }
            return false;
        }
    }

    // -- loops ---------------------------------------------------------------------

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken ct)
    {
        var buffer = new byte[16 * 1024];
        var frame = new StringBuilder();
        try
        {
            while (!ct.IsCancellationRequested && socket.State == WebSocketState.Open)
            {
                var result = await socket.ReceiveAsync(buffer, ct).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    OnRemoteClose(result.CloseStatus, result.CloseStatusDescription);
                    return;
                }
                frame.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                if (!result.EndOfMessage)
                {
                    continue;
                }
                var text = frame.ToString();
                frame.Clear();
                var evt = WsFrameParser.Parse(text);
                if (evt is null)
                {
                    _log.Warn($"ignoring malformed ws frame ({text.Length} chars)");
                    continue;
                }
                Dispatch(evt);
            }
        }
        catch (OperationCanceledException)
        {
            // deliberate teardown
        }
        catch (WebSocketException ex)
        {
            _log.Warn($"socket error: {ex.Message}");
            OnRemoteClose(null, ex.Message);
        }
        catch (Exception ex)
        {
            _log.Error("receive loop crashed", ex);
            OnRemoteClose(null, ex.Message);
        }
    }

    private void Dispatch(WsEvent evt)
    {
        switch (evt)
        {
            case WsStartedEvent started:
                _startedTcs?.TrySetResult(started.SessionId);
                break;
            case WsCompletedEvent completed:
                _completedTcs?.TrySetResult(completed);
                Completed?.Invoke(this, completed);
                break;
            case WsErrorEvent { Recoverable: false } fatal:
                // protocol-level error: server will close; fail the handshake waiter
                _startedTcs?.TrySetException(new InvalidOperationException(
                    $"server rejected session.start: {fatal.Message}"));
                break;
        }
        EventReceived?.Invoke(this, evt);
    }

    private async Task AudioSendLoopAsync(ClientWebSocket socket, CancellationToken ct)
    {
        try
        {
            await foreach (var pcm in _audio.Reader.ReadAllAsync(ct).ConfigureAwait(false))
            {
                await _sendLock.WaitAsync(ct).ConfigureAwait(false);
                try
                {
                    if (socket.State == WebSocketState.Open)
                    {
                        await socket.SendAsync(
                                new ArraySegment<byte>(pcm), WebSocketMessageType.Binary, true, ct)
                            .ConfigureAwait(false);
                    }
                    else
                    {
                        Interlocked.Increment(ref _dropped);
                    }
                }
                finally
                {
                    _sendLock.Release();
                }
            }
        }
        catch (OperationCanceledException)
        {
            // teardown
        }
        catch (WebSocketException ex)
        {
            _log.Warn($"audio send aborted: {ex.Message}");
        }
    }

    private async Task SendControlFrameAsync(string json, CancellationToken ct)
    {
        var socket = _socket;
        if (socket is null || socket.State != WebSocketState.Open)
        {
            return;
        }
        var bytes = Encoding.UTF8.GetBytes(json);
        await _sendLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await socket.SendAsync(
                new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct).ConfigureAwait(false);
        }
        finally
        {
            _sendLock.Release();
        }
    }

    // -- reconnect -----------------------------------------------------------

    private void OnRemoteClose(WebSocketCloseStatus? code, string? reason)
    {
        _startedTcs?.TrySetCanceled();
        _completedTcs?.TrySetCanceled();
        CleanupSocket();
        if (!_sessionWanted)
        {
            SetState(StreamState.Disconnected);
            return;
        }
        // 4409 (protocol) / 4400 (policy) are not worth retrying
        var terminal = code.HasValue && (int)code.Value is 4400 or 4401 or 4409;
        if (terminal)
        {
            _sessionWanted = false;
            Fault($"server closed the stream ({(int?)code ?? -1}: {reason ?? "no reason"})");
            return;
        }
        _ = Task.Run(async () =>
        {
            var ok = await TryReconnectAsync(CancellationToken.None).ConfigureAwait(false);
            if (!ok)
            {
                Fault($"connection lost and reconnect attempts exhausted ({reason ?? "no reason"})");
            }
        });
    }

    private async Task<bool> TryReconnectAsync(CancellationToken ct)
    {
        if (!BuildUri(out var uri, out _))
        {
            return false;
        }
        while (_sessionWanted && _reconnect.ShouldRetry())
        {
            var delay = _reconnect.NextDelay();
            SetState(StreamState.Reconnecting);
            _log.Info($"reconnecting in {delay.TotalSeconds:0.#} s (attempt {_reconnect.Attempts})");
            await Task.Delay(delay, ct).ConfigureAwait(false);
            try
            {
                if (State == StreamState.Reconnecting)
                {
                    await ConnectAndHandshakeAsync(uri, ct).ConfigureAwait(false);
                    if (State == StreamState.Connected)
                    {
                        return true;
                    }
                }
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _log.Warn($"reconnect attempt failed: {ex.Message}");
                CleanupSocket();
            }
        }
        return State == StreamState.Connected;
    }

    // -- plumbing --------------------------------------------------------------

    private bool BuildUri(out Uri uri, out string error)
    {
        uri = null!;
        error = "";
        if (!Uri.TryCreate(_settings.ServerUrl, UriKind.Absolute, out var http) ||
            http.Scheme is not ("http" or "https"))
        {
            error = $"invalid server URL '{_settings.ServerUrl}'";
            return false;
        }
        var path = _status.Manifest?.WsPath;
        if (string.IsNullOrEmpty(path))
        {
            path = "/ws/v1/transcribe"; // protocol default when manifest not yet fetched
        }
        var builder = new UriBuilder(http)
        {
            Scheme = http.Scheme == "https" ? "wss" : "ws",
            Path = path,
        };
        uri = builder.Uri;
        return true;
    }

    private void CleanupSocket()
    {
        try
        {
            _loopCts?.Cancel();
            _socket?.Dispose();
        }
        catch (Exception)
        {
            // teardown never throws
        }
        _socket = null;
        _loopCts = null;
    }

    private void SetState(StreamState state)
    {
        if (State == state)
        {
            return;
        }
        State = state;
        StateChanged?.Invoke(this, state);
    }

    private void Fault(string message)
    {
        _log.Error($"transcription stream faulted: {message}");
        EventReceived?.Invoke(this, new WsErrorEvent("CLIENT_STREAM_FAULT", message, false, null));
        SetState(StreamState.Faulted);
    }

    public async ValueTask DisposeAsync()
    {
        _sessionWanted = false;
        try
        {
            var socket = _socket;
            if (socket is { State: WebSocketState.Open })
            {
                await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None)
                    .ConfigureAwait(false);
            }
        }
        catch (Exception)
        {
            // best effort
        }
        CleanupSocket();
        _audio.Writer.TryComplete();
        _sendLock.Dispose();
    }
}
