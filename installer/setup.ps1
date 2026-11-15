Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$form = New-Object Windows.Forms.Form
$form.Text = 'Матилда — инсталиране'
$form.Size = New-Object Drawing.Size(630,500)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Segoe UI',10)
function Label($text,$y) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $text; $label.SetBounds(24,$y,565,42)
    $form.Controls.Add($label)
}
Label 'Матилда CRM — вашият локален бизнес асистент' 20
Label 'Папка за инсталиране:' 67
$target = New-Object Windows.Forms.TextBox
$target.Text = Join-Path $env:LOCALAPPDATA 'Matilda'
$target.SetBounds(24,99,470,28); $form.Controls.Add($target)
$choose = New-Object Windows.Forms.Button
$choose.Text = 'Избери'; $choose.SetBounds(505,98,85,30); $form.Controls.Add($choose)
$choose.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Изберете папка за Матилда'
    if ($dialog.ShowDialog() -eq 'OK') { $target.Text = $dialog.SelectedPath }
})
Label 'Наличен Phi-4-mini-instruct (файловете остават на мястото си):' 141
$model = New-Object Windows.Forms.TextBox
$model.SetBounds(24,174,470,28); $form.Controls.Add($model)
$cacheRoot = Join-Path $env:USERPROFILE '.cache\huggingface\hub\models--microsoft--Phi-4-mini-instruct\snapshots'
$found = Get-ChildItem -LiteralPath $cacheRoot -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'model.safetensors.index.json') } | Select-Object -First 1
if ($found) { $model.Text = $found.FullName }
$browseModel = New-Object Windows.Forms.Button
$browseModel.Text = 'Избери'; $browseModel.SetBounds(505,173,85,30); $form.Controls.Add($browseModel)
$browseModel.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Папката с config.json и оригиналните safetensors файлове на Phi'
    if ($dialog.ShowDialog() -eq 'OK') { $model.Text = $dialog.SelectedPath }
})
$download = New-Object Windows.Forms.CheckBox
$download.Text = 'Ако нямам Phi: изтегли модела (около 8 GB)'
$download.SetBounds(24,213,565,30); $form.Controls.Add($download)
$speech = New-Object Windows.Forms.CheckBox
$speech.Text = 'Инсталирай и гласови бележки (Whisper small, около 500 MB)'
$speech.Checked = $true; $speech.SetBounds(24,246,565,30); $form.Controls.Add($speech)
$status = New-Object Windows.Forms.Label
$status.Text = 'Нужен е интернет за първата инсталация. Данните остават локални.'
$status.SetBounds(24,288,565,55); $form.Controls.Add($status)
$progress = New-Object Windows.Forms.ProgressBar
$progress.SetBounds(24,348,565,18); $form.Controls.Add($progress)
$install = New-Object Windows.Forms.Button
$install.Text = 'Инсталирай и отвори'; $install.SetBounds(355,387,235,40); $form.Controls.Add($install)
$script:worker = $null
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if (Test-Path -LiteralPath $script:progressPath) {
        try {
            $info = Get-Content -LiteralPath $script:progressPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $status.Text = $info.message; $progress.Value = [Math]::Min(100,[int]$info.percent)
        } catch {}
    }
    if ($script:worker -and $script:worker.HasExited) {
        $timer.Stop(); $install.Enabled = $true
        if ($script:worker.ExitCode -eq 0) { $form.Close() }
        else { [Windows.Forms.MessageBox]::Show("Инсталацията спря. Подробности: $script:logPath",'Матилда') }
    }
})
$install.Add_Click({
    try {
        if (!$model.Text.Trim() -and !$download.Checked) { throw 'Изберете наличния модел или отметнете изтеглянето.' }
        $destination = [IO.Path]::GetFullPath($target.Text.Trim())
        if ($destination -eq [IO.Path]::GetPathRoot($destination)) { throw 'Изберете отделна папка, например D:\Matilda.' }
        $stateDir = Join-Path ([IO.Path]::GetTempPath()) ('matilda-setup-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($stateDir) | Out-Null
        $script:progressPath = Join-Path $stateDir 'progress.json'
        $script:logPath = Join-Path $stateDir 'install.log'
        $requestPath = Join-Path $stateDir 'request.json'
        @{target=$destination;source=$sourceRoot;model=$model.Text.Trim();downloadPhi=$download.Checked;speech=$speech.Checked;progress=$script:progressPath;log=$script:logPath} | ConvertTo-Json | Set-Content -LiteralPath $requestPath -Encoding UTF8
        $workerScript = Join-Path $PSScriptRoot 'install-worker.ps1'
        $script:worker = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $workerScript + '" -RequestFile "' + $requestPath + '"')
        $install.Enabled = $false; $status.Text = 'Подготвяме инсталацията…'; $timer.Start()
    } catch { [Windows.Forms.MessageBox]::Show($_.Exception.Message,'Матилда') }
})
$form.Add_FormClosing({
    if ($script:worker -and !$script:worker.HasExited) { $_.Cancel = $true; $status.Text = 'Инсталацията продължава. Изчакайте да завърши.' }
})
[void]$form.ShowDialog()
