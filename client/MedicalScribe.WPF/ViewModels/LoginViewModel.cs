using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using MedicalScribe.WPF.Infrastructure;
using MedicalScribe.WPF.Services;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class LoginViewModel : ObservableObject
{
    private readonly IAuthorizationService _auth;
    private readonly IServerStatusService _status;
    private readonly ILoggerService _logger;

    public LoginViewModel(
        IAuthorizationService auth,
        IServerStatusService status,
        ILoggerService logger)
    {
        _auth = auth;
        _status = status;
        _logger = logger;
        _status.StateChanged += (_, _) =>
        {
            if (System.Windows.Application.Current?.Dispatcher is { } d && !d.CheckAccess())
            {
                d.BeginInvoke(() => OnPropertyChanged(nameof(ConnectionHint)));
            }
            else
            {
                OnPropertyChanged(nameof(ConnectionHint));
            }
        };
    }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _username = "";

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private string _password = "";

    [ObservableProperty]
    private string? _errorMessage;

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(LoginCommand))]
    private bool _isBusy;

    public string ConnectionHint => _status.State switch
    {
        ConnectionState.Online => "Server reachable",
        ConnectionState.Offline => "Server unreachable — start the backend or fix the URL in settings",
        ConnectionState.Degraded => "Server reachable, API degraded",
        _ => "Checking connection…",
    };

    private bool CanLogin() =>
        !IsBusy && !string.IsNullOrWhiteSpace(Username) && !string.IsNullOrWhiteSpace(Password);

    [RelayCommand(CanExecute = nameof(CanLogin))]
    private async Task LoginAsync()
    {
        ErrorMessage = null;
        IsBusy = true;
        try
        {
            await _auth.LoginAsync(Username.Trim(), Password);
            Password = string.Empty;
        }
        catch (ApiException ex) when (ex.Code == "AUTH_NOT_IMPLEMENTED")
        {
            ErrorMessage =
                "Connected to server. Username/password auth lands in Phase 7 — " +
                "dev builds: sign in with user \"dev\" and the MS_AUTH__DEV_TOKEN value.";
        }
        catch (ApiException ex) when (ex.Code == "NETWORK")
        {
            ErrorMessage = $"Cannot reach server: {ex.DetailMessage}";
        }
        catch (ApiException ex) when (ex.Code is "UNAUTHENTICATED" or "FORBIDDEN")
        {
            ErrorMessage = "Invalid credentials.";
        }
        catch (ApiException ex)
        {
            ErrorMessage = $"Login failed ({ex.Code}).";
            _logger.Error("login failed", ex);
        }
        finally
        {
            IsBusy = false;
        }
    }
}
