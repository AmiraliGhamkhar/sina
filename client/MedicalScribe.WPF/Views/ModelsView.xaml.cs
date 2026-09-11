/* Phase 5 CI hardening: the WPF markup-compile temp project on Linux drops
   ImplicitUsings items, so this file lists them explicitly (duplicates from
   the SDK's implicit set are warnings at worst; TreatWarningsAsErrors=false). */
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

using System.Windows.Controls;

namespace MedicalScribe.WPF.Views;

public partial class ModelsView : UserControl
{
    public ModelsView()
    {
        InitializeComponent();
    }
}
