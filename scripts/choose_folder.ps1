# Invoice folder chooser using the native Windows folder dialog.
# Optional default directory is passed via the environment variable FAPIAO_DEFAULT_DIR.
Add-Type -AssemblyName System.Windows.Forms
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = "Select Invoice Folder"
$dlg.ShowNewFolderButton = $true
$defaultDir = $env:FAPIAO_DEFAULT_DIR
if ($defaultDir -and (Test-Path -LiteralPath $defaultDir)) {
    try { $dlg.SelectedPath = $defaultDir } catch { }
}
$result = $dlg.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dlg.SelectedPath
}
