// Exponential-backoff reconnect policy — pure, testable, no timers inside
// (the caller sleeps the returned delay). Backoff 2^n × initial capped at
// MaxDelay; a session that connected successfully resets the counter.
namespace MedicalScribe.WPF.Services;

public sealed class ReconnectPolicy
{
    private readonly int _maxAttempts;
    private readonly TimeSpan _initialDelay;
    private readonly TimeSpan _maxDelay;
    private readonly double _factor;
    private int _attempt;

    public ReconnectPolicy(
        int maxAttempts = 5,
        TimeSpan? initialDelay = null,
        TimeSpan? maxDelay = null,
        double factor = 2.0)
    {
        _maxAttempts = Math.Max(0, maxAttempts);
        _initialDelay = initialDelay ?? TimeSpan.FromMilliseconds(500);
        _maxDelay = maxDelay ?? TimeSpan.FromSeconds(30);
        _factor = factor <= 1 ? 1 : factor;
    }

    public int Attempts => _attempt;

    public bool ShouldRetry() => _attempt < _maxAttempts;

    /// <summary>Delay before attempt N (0-based), advancing the counter.</summary>
    public TimeSpan NextDelay()
    {
        var ms = _initialDelay.TotalMilliseconds * Math.Pow(_factor, _attempt);
        _attempt++;
        var capped = Math.Min(ms, _maxDelay.TotalMilliseconds);
        return TimeSpan.FromMilliseconds(capped);
    }

    public void Reset() => _attempt = 0;
}
