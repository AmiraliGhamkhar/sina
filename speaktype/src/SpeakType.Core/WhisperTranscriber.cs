using System;
using System.Diagnostics;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace SpeakType.Core
{
    public class WhisperTranscriber : IWhisperTranscriber
    {
        public async Task<string> TranscribeAsync(string audioFilePath, string modelPath, string whisperPath, CancellationToken cancellationToken = default)
        {
            if (!System.IO.File.Exists(whisperPath))
            {
                throw new System.IO.FileNotFoundException($"Whisper executable not found at {whisperPath}");
            }
            
            if (!System.IO.File.Exists(modelPath))
            {
                 throw new System.IO.FileNotFoundException($"Whisper model not found at {modelPath}");
            }

            var startInfo = new ProcessStartInfo
            {
                FileName = whisperPath,
                // -m: model path
                // -f: input file
                // -nt: no timestamps (output plain text)
                Arguments = $"-m \"{modelPath}\" -f \"{audioFilePath}\" -nt",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8
            };

            using var process = new Process { StartInfo = startInfo };
            var outputBuilder = new StringBuilder();
            var errorBuilder = new StringBuilder();

            var tcs = new TaskCompletionSource<int>();

            process.OutputDataReceived += (sender, args) =>
            {
                if (args.Data != null)
                {
                    outputBuilder.AppendLine(args.Data);
                }
            };
            
            process.ErrorDataReceived += (sender, args) =>
            {
                if (args.Data != null)
                {
                    errorBuilder.AppendLine(args.Data);
                }
            };

            process.EnableRaisingEvents = true;
            process.Exited += (sender, args) => tcs.TrySetResult(process.ExitCode);

            if (!process.Start())
            {
                throw new Exception("Failed to start Whisper process.");
            }

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            using var registration = cancellationToken.Register(() =>
            {
                if (!process.HasExited)
                {
                    try { process.Kill(); } catch { }
                }
                tcs.TrySetCanceled();
            });

            int exitCode = await tcs.Task.ConfigureAwait(false);

            if (exitCode != 0)
            {
                throw new Exception($"Whisper exited with code {exitCode}. Error: {errorBuilder}");
            }

            return outputBuilder.ToString().Trim();
        }
    }
}
