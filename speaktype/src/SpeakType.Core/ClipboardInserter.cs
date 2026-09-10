using System.Threading.Tasks;
using System.Windows.Forms;
using System.Runtime.InteropServices;

namespace SpeakType.Core
{
    public class ClipboardInserter : IClipboardInserter
    {
        private readonly ILogger? _logger;
        private const int MaxRetryAttempts = 5;
        private const int RetryDelayMs = 5;
        private const int PasteCompletionDelayMs = 10;

        public ClipboardInserter(ILogger? logger = null)
        {
            _logger = logger;
        }

        public async Task InsertTextAsync(string text)
        {
            if (string.IsNullOrEmpty(text)) return;

            _logger?.Log($"[ClipboardInserter] Starting insertion of: '{text}'");

            try
            {
                // Set text
                // Retry loop for setting text as Clipboard can be busy
                bool setSuccess = false;
                for (int i = 0; i < MaxRetryAttempts; i++)
                {
                    try
                    {
                        Clipboard.SetText(text);
                        setSuccess = true;
                        _logger?.Log($"[ClipboardInserter] Clipboard set successfully on attempt {i + 1}");
                        break;
                    }
                    catch (ExternalException ex)
                    {
                        _logger?.Log($"[ClipboardInserter] Clipboard set failed (attempt {i + 1}): {ex.Message}");
                        await Task.Delay(RetryDelayMs);
                    }
                }

                if (setSuccess)
                {
                    // Send Paste
                    _logger?.Log("[ClipboardInserter] Sending Ctrl+V...");
                    await InputSender.SendCtrlV();
                    _logger?.Log("[ClipboardInserter] Ctrl+V sent");
                }
                else
                {
                    _logger?.LogError("[ClipboardInserter] Failed to set clipboard after maximum attempts", null);
                }
            }
            finally
            {
                // Leave transcribed text on clipboard as a fallback for manual paste
                // Small delay to allow paste to complete
                await Task.Delay(PasteCompletionDelayMs);
                _logger?.Log("[ClipboardInserter] Transcribed text left on clipboard for manual paste if needed");
            }
        }
    }
}
