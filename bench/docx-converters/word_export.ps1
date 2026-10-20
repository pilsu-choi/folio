# Export reference PDFs with Microsoft Word (COM). Run on Windows (or from WSL via powershell.exe).
# usage: powershell -NoProfile -ExecutionPolicy Bypass -File word_export.ps1 C:\path\samples[\one.docx] C:\path\ref
# Refuses to run while another Word process is open, so the user's documents are never touched.
param([Parameter(Mandatory)][string]$In, [Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = "Stop"
if (Get-Process WINWORD -ErrorAction SilentlyContinue) { throw "Close Word first (WINWORD is running)" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$word = New-Object -ComObject Word.Application
try {
    $word.Visible = $false
    $word.DisplayAlerts = 0                # wdAlertsNone
    $word.AutomationSecurity = 3           # msoAutomationSecurityForceDisable (no macros)
    "word $($word.Build)"
    foreach ($f in Get-ChildItem -Path $In -Filter *.docx | Sort-Object Name) {
        [string]$dst = Join-Path $Out ($f.BaseName + ".pdf")  # a PSObject-wrapped path hangs ExportAsFixedFormat
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles)
            $doc = $word.Documents.Open($f.FullName, $false, $true, $false)
            $doc.ExportAsFixedFormat($dst, 17)  # wdExportFormatPDF, default: document content without markup
            $pages = $doc.ComputeStatistics(2)  # wdStatisticPages
            $doc.Close(0)                       # wdDoNotSaveChanges
            "ok $($f.BaseName) pages=$pages ms=$($sw.ElapsedMilliseconds)"
        } catch { "FAIL $($f.BaseName) $($_.Exception.Message)" }
    }
} finally { $word.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
