// Patient/Encounter selection screen. Persistence + search land with the
// PostgreSQL layer (Phase 7); the form shape here mirrors the backend
// schemas so Phase 7 only implements the repository.
using CommunityToolkit.Mvvm.ComponentModel;

namespace MedicalScribe.WPF.ViewModels;

public sealed partial class PatientEncounterViewModel : ObservableObject
{
    [ObservableProperty]
    private string _patientSearch = "";

    [ObservableProperty]
    private string _patientMrn = "";

    [ObservableProperty]
    private string _patientName = "";

    [ObservableProperty]
    private string _encounterType = "clinical-visit";

    /// <summary>Encounter privacy flag → drives AI routing server-side
    /// (private=true must force local providers; spec §11).</summary>
    [ObservableProperty]
    private bool _privateEncounter = true;

    [ObservableProperty]
    private bool _persistenceDisabledNoticeVisible = true;

    public string[] EncounterTypes { get; } =
    [
        "clinical-visit", "radiology", "ultrasound", "ct", "mri", "follow-up",
    ];

    public string PersistenceHint =>
        "Patient/encounter persistence (search, storage) lands in Phase 7 — " +
        "the selected context is validated client-side today and attached to sessions in Phase 2.";
}
