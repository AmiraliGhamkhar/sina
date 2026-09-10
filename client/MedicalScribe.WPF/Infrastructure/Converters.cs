// Small value converters used by the views. Kept minimal; anything more
// complex belongs in a view-model.
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.Infrastructure;

public sealed class ConnectionStateToBrushConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is ConnectionState state
            ? state switch
            {
                ConnectionState.Online => new SolidColorBrush(Color.FromRgb(0x16, 0xA3, 0x4A)),
                ConnectionState.Degraded => new SolidColorBrush(Color.FromRgb(0xD9, 0x77, 0x06)),
                ConnectionState.Offline => new SolidColorBrush(Color.FromRgb(0xDC, 0x26, 0x26)),
                _ => new SolidColorBrush(Color.FromRgb(0x94, 0xA3, 0xB8)),
            }
            : new SolidColorBrush(Colors.Gray);

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public sealed class BoolToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is true ? Visibility.Visible : Visibility.Collapsed;

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is Visibility.Visible;
}

public sealed class InverseBoolToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is true ? Visibility.Collapsed : Visibility.Visible;

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public sealed class NullToCollapsedConverter : IValueConverter
{
    public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        value is null ? Visibility.Collapsed : Visibility.Visible;

    public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}
