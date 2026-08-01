<#
.SYNOPSIS
  ebook-audiobook installer for Windows.

.DESCRIPTION
  Run this in PowerShell:

    irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.ps1 | iex

  What it does, in order:
    1. finds a Python 3.11+ interpreter (offers to install one via winget)
    2. creates a private virtualenv under %LOCALAPPDATA%\ebook-audiobook
    3. installs the app, plus the right PyTorch build for this machine
    4. checks for Calibre and offers to install it
    5. adds an `ebook-audiobook` command and a Start Menu shortcut

  Everything is per-user. No administrator rights are needed, nothing is written
  outside your own profile, and your books and settings are never touched by an
  upgrade or an uninstall.

.PARAMETER Version
  Install a specific release instead of the latest.

.PARAMETER Cpu
  Force the CPU-only PyTorch build (a much smaller download).

.PARAMETER NoTts
  Skip PyTorch entirely. You can import books but not render audio yet.

.PARAMETER Yes
  Accept all prompts, for scripted installs.

.PARAMETER Uninstall
  Remove the program. Your books and settings are kept.
#>
[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$InstallDir = "",
    [switch]$Cpu,
    [switch]$NoTts,
    [switch]$Yes,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$Repo = "denelson1-dot/ebook-audiobook"
# The release workflow rewrites this line in the published copy of this script,
# so the installer always knows exactly which wheel it belongs to. A wheel's
# filename must contain its version to be installable, so a fixed
# "latest/download/..." asset name is not an option; baking the version in beats
# calling the GitHub API, which is rate-limited for unauthenticated users.
$PinnedVersion = "__EBAB_VERSION__"

# --- output helpers ----------------------------------------------------------
function Write-Step($msg) { Write-Host ""; Write-Host "==> " -ForegroundColor Green -NoNewline; Write-Host $msg -ForegroundColor White }
function Write-Ok($msg)   { Write-Host "  [ok] " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn($msg) { Write-Host "  [!] " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Write-Dim($msg)  { Write-Host "  $msg" -ForegroundColor DarkGray }
function Fail($msg) { Write-Host ""; Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

function Ask($question, $default = "y") {
    $hint = if ($default -eq "y") { "[Y/n]" } else { "[y/N]" }
    if ($Yes) { Write-Host "  $question $hint y (auto)"; return ($default -eq "y") }
    $reply = Read-Host "  $question $hint"
    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $default }
    return ($reply.Trim().ToLower() -in @("y", "yes"))
}

# --- locations ---------------------------------------------------------------
$DataDir = if ($InstallDir) { $InstallDir } else { Join-Path $env:LOCALAPPDATA "ebook-audiobook" }
$VenvDir = Join-Path $DataDir "venv"
$BinDir  = Join-Path $DataDir "bin"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"

# --- uninstall ---------------------------------------------------------------
if ($Uninstall) {
    Write-Step "Uninstalling ebook-audiobook"
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
    if (Test-Path $BinDir)  { Remove-Item -Recurse -Force $BinDir }
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\ebook-audiobook.lnk"
    if (Test-Path $startMenu) { Remove-Item -Force $startMenu }
    $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "ebook-audiobook.lnk"
    if (Test-Path $desktopLnk) { Remove-Item -Force $desktopLnk }

    # Take our entry back out of the user PATH, leaving everything else alone.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -and $userPath.Split(';') -contains $BinDir) {
        $kept = ($userPath.Split(';') | Where-Object { $_ -and $_ -ne $BinDir }) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $kept, "User")
    }
    Write-Ok "program removed"
    Write-Host ""
    Write-Host "  Your books, settings, and audiobooks were NOT deleted. They're in:"
    Write-Host "    $DataDir"
    Write-Host "  Delete that folder yourself if you want them gone."
    exit 0
}

Write-Host ""
Write-Host "ebook-audiobook installer" -ForegroundColor White
Write-Host "Turns ebooks you own into narrated audiobooks, entirely offline." -ForegroundColor DarkGray

# --- 1. Python ---------------------------------------------------------------
Write-Step "Looking for Python 3.11 or newer"

function Test-PythonVersion($exe) {
    try {
        # 3.11+ or nothing: older versions can't run the app.
        & $exe -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    # The py launcher is the reliable way to pick a version on Windows; asking
    # for `python` alone can hit the Microsoft Store stub, which is not a real
    # interpreter and silently does nothing useful.
    foreach ($v in @("3.13", "3.12", "3.11")) {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p)) { return $p }
        } catch {}
    }
    foreach ($name in @("python3.13", "python3.12", "python3.11", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            # Store stubs live under WindowsApps and are 0 bytes of nothing.
            if ($cmd.Source -like "*WindowsApps*") { continue }
            if (Test-PythonVersion $cmd.Source) { return $cmd.Source }
        }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Warn "no Python 3.11+ found"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install Python 3.12 now with winget?" "y") {
            winget install --id Python.Python.3.12 --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            # winget updates PATH for new processes only; refresh ours so the
            # freshly installed interpreter is findable without a restart.
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [Environment]::GetEnvironmentVariable("Path", "User")
            $Python = Find-Python
        }
    }
}
if (-not $Python) {
    Fail "Python 3.11+ is required.`n       Install it from https://www.python.org/downloads/`n       (tick 'Add python.exe to PATH'), then re-run this installer."
}
$pyVer = & $Python -c "import platform; print(platform.python_version())"
Write-Ok "Python $pyVer at $Python"

# --- 2. virtualenv -----------------------------------------------------------
Write-Step "Creating a private environment"
Write-Dim $VenvDir
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
if (Test-Path $VenvPy) {
    Write-Ok "reusing the existing environment (upgrading in place)"
} else {
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "couldn't create a virtualenv at $VenvDir" }
    Write-Ok "created"
}
if (-not (Test-Path $VenvPy)) { Fail "the environment at $VenvDir looks broken; delete it and re-run" }
& $VenvPy -m pip install --quiet --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Fail "couldn't upgrade pip" }

# --- 3. the app --------------------------------------------------------------
Write-Step "Installing ebook-audiobook"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($scriptDir -and (Test-Path (Join-Path $scriptDir "pyproject.toml"))) {
    # Running from a checkout: install from source. Used by CI to smoke-test this
    # installer, and by anyone testing a change before cutting a release.
    Write-Dim "installing from the source tree at $scriptDir"
    & $VenvPy -m pip install --quiet $scriptDir
} else {
    $resolved = $Version
    if ($resolved -eq "latest") {
        if ($PinnedVersion -notlike "__EBAB_*") {
            $resolved = $PinnedVersion
        } else {
            # Unreleased copy of this script: ask GitHub what's current.
            try {
                $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
                                         -Headers @{ "User-Agent" = "ebook-audiobook-installer" }
                $resolved = $rel.tag_name -replace '^v', ''
            } catch {
                Fail "couldn't work out which version to install.`n       Pass one explicitly, e.g. -Version 1.0.0`n       Releases: https://github.com/$Repo/releases"
            }
        }
    }
    $wheelUrl = "https://github.com/$Repo/releases/download/v$resolved/ebook_audiobook-$resolved-py3-none-any.whl"
    Write-Dim $wheelUrl
    & $VenvPy -m pip install --quiet --upgrade $wheelUrl
}
if ($LASTEXITCODE -ne 0) { Fail "couldn't install the app. Check your connection, or see https://github.com/$Repo/releases" }
$appVer = & $VenvPy -c "import importlib.metadata as m; print(m.version('ebook-audiobook'))"
Write-Ok "installed $appVer"

# --- 4. PyTorch --------------------------------------------------------------
if ($NoTts) {
    Write-Step "Skipping the speech engine (-NoTts)"
    Write-Warn "you can import books, but rendering audio needs the engine"
    Write-Dim "add it later with: `"$VenvDir\Scripts\pip.exe`" install `"ebook-audiobook[tts]`""
} else {
    Write-Step "Setting up the speech engine"
    $hasNvidia = $false
    $gpuName = ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $gpuName) { $hasNvidia = $true }
        } catch {}
    }

    if ($Cpu) {
        $desc = "CPU only (forced with -Cpu)"; $size = "about 250 MB"
        $index = "https://download.pytorch.org/whl/cpu"
    } elseif ($hasNvidia) {
        $desc = "$($gpuName.Trim()) via CUDA - a novel takes roughly 2-3 hours"
        $size = "about 2.5 GB"
        $index = "https://download.pytorch.org/whl/cu124"
    } else {
        $desc = "no NVIDIA GPU detected, CPU only - a novel can take many hours"
        $size = "about 250 MB"
        $index = "https://download.pytorch.org/whl/cpu"
    }

    Write-Host "  Detected: " -NoNewline; Write-Host $desc -ForegroundColor White
    Write-Host "  Download: " -NoNewline; Write-Host $size -ForegroundColor White
    if (Ask "Download and install the speech engine now?" "y") {
        & $VenvPy -m pip install --quiet torch torchaudio --index-url $index
        if ($LASTEXITCODE -ne 0) { Fail "PyTorch install failed. Re-run with -Cpu, or install torch manually." }
        & $VenvPy -m pip install --quiet "ebook-audiobook[tts]"
        if ($LASTEXITCODE -ne 0) {
            & $VenvPy -m pip install --quiet chatterbox-tts "setuptools<81"
            if ($LASTEXITCODE -ne 0) { Write-Warn "the Chatterbox engine didn't install; retry with: `"$VenvDir\Scripts\pip.exe`" install chatterbox-tts" }
        }
        Write-Ok "speech engine ready"
        Write-Dim "The ~1 GB voice model downloads the first time you render."
    } else {
        Write-Warn "skipped - add it later with: `"$VenvDir\Scripts\pip.exe`" install `"ebook-audiobook[tts]`""
    }
}

# --- 5. Calibre --------------------------------------------------------------
Write-Step "Checking for Calibre (needed to read ebook files)"
& $VenvPy -c "from ebook_audiobook import tools; raise SystemExit(0 if tools.ebook_convert_path() else 1)" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Calibre found"
} else {
    Write-Warn "Calibre is not installed"
    $installed = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if (Ask "Install it now with 'winget install calibre.calibre'?" "y") {
            winget install --id calibre.calibre --source winget `
                --accept-package-agreements --accept-source-agreements --silent
            if ($LASTEXITCODE -eq 0) { $installed = $true } else { Write-Warn "winget install failed" }
        }
    }
    if ($installed) {
        Write-Ok "Calibre installed"
    } else {
        Write-Warn "install Calibre before converting a book:"
        Write-Host "      winget install --id calibre.calibre"
        Write-Dim "or download it from https://calibre-ebook.com/download"
    }
}

# --- 6. launchers ------------------------------------------------------------
Write-Step "Creating the launcher"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

# A .cmd shim rather than a copied .exe, so upgrading the venv can never leave a
# stale launcher pointing at a deleted interpreter.
$shim = Join-Path $BinDir "ebook-audiobook.cmd"
@"
@echo off
REM Generated by the ebook-audiobook installer.
"$VenvDir\Scripts\ebook-audiobook.exe" %*
"@ | Set-Content -Path $shim -Encoding ASCII
Write-Ok "command: ebook-audiobook"

# Put the shim on the *user* PATH (never the machine PATH: no admin, no
# surprises for other accounts).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath.Split(';') -notcontains $BinDir) {
    $newPath = if ($userPath.TrimEnd(';')) { "$($userPath.TrimEnd(';'));$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$BinDir"
    Write-Ok "added to your PATH (new terminals will find it)"
}

# Start Menu shortcut. Points at the gui_scripts entry point, which Windows runs
# with pythonw.exe, so opening it doesn't leave a console window behind the
# browser.
$guiExe = Join-Path $VenvDir "Scripts\ebook-audiobook-gui.exe"
$targetExe = if (Test-Path $guiExe) { $guiExe } else { Join-Path $VenvDir "Scripts\ebook-audiobook.exe" }
try {
    $shell = New-Object -ComObject WScript.Shell
    $startMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    New-Item -ItemType Directory -Force -Path $startMenuDir | Out-Null
    $lnk = $shell.CreateShortcut((Join-Path $startMenuDir "ebook-audiobook.lnk"))
    $lnk.TargetPath = $targetExe
    $lnk.WorkingDirectory = $DataDir
    $lnk.Description = "Turn ebooks you own into narrated audiobooks"
    $lnk.Save()
    Write-Ok "Start Menu shortcut"

    if (Ask "Add a Desktop shortcut too?" "y") {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $dlnk = $shell.CreateShortcut((Join-Path $desktop "ebook-audiobook.lnk"))
        $dlnk.TargetPath = $targetExe
        $dlnk.WorkingDirectory = $DataDir
        $dlnk.Description = "Turn ebooks you own into narrated audiobooks"
        $dlnk.Save()
        Write-Ok "Desktop shortcut"
    }
} catch {
    Write-Warn "couldn't create shortcuts: $_"
}

# --- done --------------------------------------------------------------------
Write-Step "Verifying the install"
& $VenvPy -m ebook_audiobook.cli check --engine fake > $null 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Ok "all required components are working"
} else {
    Write-Warn "some checks failed - run 'ebook-audiobook check' for details"
}

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host ""
Write-Host "  Start it from the Start Menu, or run: " -NoNewline; Write-Host "ebook-audiobook" -ForegroundColor White
Write-Host "  Your books live in: " -NoNewline; Write-Host $DataDir -ForegroundColor DarkGray
Write-Host "  Check setup:  " -NoNewline; Write-Host "ebook-audiobook check" -ForegroundColor DarkGray
Write-Host "  Uninstall:    " -NoNewline; Write-Host "iex `"& { `$(irm https://github.com/$Repo/releases/latest/download/install.ps1) } -Uninstall`"" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Note: open a NEW terminal for the 'ebook-audiobook' command to be found." -ForegroundColor DarkGray
Write-Host ""
