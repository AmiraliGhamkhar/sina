using System.Threading.Tasks;

namespace SpeakType.Core
{
    public interface IClipboardInserter
    {
        Task InsertTextAsync(string text);
    }
}
