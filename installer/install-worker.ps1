param([Parameter(Mandatory=$true)][string]$RequestFile)
$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath $RequestFile -Raw -Encoding UTF8 | ConvertFrom-Json
function Progress($percent,$message) {
    @{percent=$percent;message=$message} | ConvertTo-Json | Set-Content -LiteralPath $request.progress -Encoding UTF8
    Add-Content -LiteralPath $request.log -Value $message -Encoding UTF8
}
function Run($exe, [string[]]$arguments) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @arguments 2>&1 | ForEach-Object { Add-Content -LiteralPath $request.log -Value ([string]$_) -Encoding UTF8 }; $code = $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
    if ($code -ne 0) { throw "Command failed ($code). See install.log." }
}
try {
    Progress 3 'Проверяваме папката и наличния модел…'
    $destination = [IO.Path]::GetFullPath($request.target).TrimEnd('\')
    $source = [IO.Path]::GetFullPath($request.source).TrimEnd('\')
    if (!$destination -or $destination -eq [IO.Path]::GetPathRoot($request.target).TrimEnd('\')) { throw 'Choose a dedicated installation folder.' }
    if ($destination -eq $source) { throw 'Choose a separate installation folder, not the unpacked package.' }
    if ((Test-Path -LiteralPath $destination) -and (Get-ChildItem -LiteralPath $destination -Force | Select-Object -First 1)) {
        throw 'The destination must be empty. Choose a new folder; existing CRM data will not be overwritten.'
    }
    if ($request.model) {
        if (!(Test-Path -LiteralPath (Join-Path $request.model 'config.json')) -or !(Test-Path -LiteralPath (Join-Path $request.model 'model.safetensors.index.json'))) { throw 'This folder is not the original Phi safetensors model.' }
    } elseif (!$request.downloadPhi) { throw 'Select the existing Phi model.' }
    [IO.Directory]::CreateDirectory($destination) | Out-Null
    # Explicit source allowlist: no data, credentials, personal config, caches or models.
    foreach ($folder in @('crm','static','installer','docs')) {
        Copy-Item -LiteralPath (Join-Path $source $folder) -Destination $destination -Recurse
    }
    foreach ($file in @('launcher.py','requirements.txt','requirements-model.txt','START_CRM.vbs','START_CRM.cmd','START_PHONE_ACCESS.cmd','SETUP_PHONE.cmd','README.md','LICENSE','SECURITY.md','CONTRIBUTING.md')) {
        Copy-Item -LiteralPath (Join-Path $source $file) -Destination $destination
    }
    Progress 10 'Подготвяме Python в избраната папка…'
    $python = $null
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $ErrorActionPreference = 'Continue'
            $candidate = & $py.Source -3.12 -c 'import sys;print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) { $candidates += $candidate }
        } finally { $ErrorActionPreference = 'Stop' }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            & $candidate -c 'import sys,struct;sys.exit(0 if sys.version_info[:2]==(3,12) and struct.calcsize(chr(80))==8 else 1)'
            if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
        }
    }
    if (!$python) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $installer = Join-Path (Split-Path $RequestFile) 'python-3.12.10-amd64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python installer signature validation failed.' }
        $pythonDir = Join-Path $destination '.python'
        $setup = Start-Process -FilePath $installer -WindowStyle Hidden -Wait -PassThru -ArgumentList ('/quiet InstallAllUsers=0 Include_launcher=0 Include_test=0 PrependPath=0 TargetDir="' + $pythonDir + '"')
        if ($setup.ExitCode -notin @(0,3010)) { throw 'Python installation failed.' }
        $python = Join-Path $pythonDir 'python.exe'
    }
    Run $python @('-m','venv',(Join-Path $destination '.venv'))
    $venv = Join-Path $destination '.venv\Scripts\python.exe'
    Progress 25 'Инсталираме компонентите на CRM…'
    Run $venv @('-m','pip','install','--disable-pip-version-check','-r',(Join-Path $destination 'requirements.txt'))
    Progress 40 'Инсталираме локалния AI runtime. Това може да отнеме няколко минути…'
    $cuda = [bool](Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)
    $wheelIndex = if ($cuda) { 'https://download.pytorch.org/whl/cu121' } else { 'https://download.pytorch.org/whl/cpu' }
    Run $venv @('-m','pip','install','torch==2.5.1','--index-url',$wheelIndex)
    Run $venv @('-m','pip','install','-r',(Join-Path $destination 'requirements-model.txt'))
    Progress 65 'Свързваме модела и подготвяме гласовите бележки…'
    Run $venv @((Join-Path $destination 'installer\configure.py'),'--request',$RequestFile,'--root',$destination)
    Progress 95 'Създаваме пряк път и отваряме Матилда…'
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath('Desktop')
    $link = $shell.CreateShortcut((Join-Path $desktop 'Матилда.lnk'))
    $link.TargetPath = Join-Path $destination '.venv\Scripts\pythonw.exe'
    $link.Arguments = '-B "' + (Join-Path $destination 'launcher.py') + '"'
    $link.WorkingDirectory = $destination; $link.WindowStyle = 7; $link.Save()
    [IO.File]::WriteAllText((Join-Path $destination '.matilda-install.json'),'{"version":"0.2.0"}')
    Start-Process -FilePath $link.TargetPath -ArgumentList $link.Arguments -WorkingDirectory $destination -WindowStyle Hidden
    Progress 100 'Готово. Матилда се отваря в браузъра.'
    Copy-Item -LiteralPath $request.log -Destination (Join-Path $destination 'install.log')
    exit 0
} catch {
    Progress 0 ('Грешка: ' + $_.Exception.Message)
    exit 1
}
