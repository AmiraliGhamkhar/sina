using System;
using System.Linq;
using NAudio.Wave;

namespace SpeakType.Core
{
    public static class AudioDeviceHelper
    {
        public static void ListDevices(ILogger logger)
        {
            logger.Log("=== Available Recording Devices ===");
            for (int i = 0; i < WaveInEvent.DeviceCount; i++)
            {
                var caps = WaveInEvent.GetCapabilities(i);
                logger.Log($"Device #{i}: {caps.ProductName} ({caps.Channels} channels)");
            }
            logger.Log("===================================");
        }

        public static int? FindBestMicrophoneDevice()
        {
            // Try to find a device that's NOT the Steam Streaming Mic
            for (int i = 0; i < WaveInEvent.DeviceCount; i++)
            {
                var caps = WaveInEvent.GetCapabilities(i);
                var name = caps.ProductName.ToLower();
                
                // Skip Steam devices
                if (name.Contains("steam")) continue;
                
                // Prefer devices with "microphone" in the name
                if (name.Contains("microphone") || name.Contains("mic"))
                {
                    return i;
                }
            }
            
            // If no microphone found, just use device 0 (but not if it's Steam)
            if (WaveInEvent.DeviceCount > 0)
            {
                var caps = WaveInEvent.GetCapabilities(0);
                if (!caps.ProductName.ToLower().Contains("steam"))
                {
                    return 0;
                }
            }
            
            // Last resort: try device 1 if it exists
            return WaveInEvent.DeviceCount > 1 ? 1 : 0;
        }
    }
}
