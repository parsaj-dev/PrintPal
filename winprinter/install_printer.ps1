<#
.SYNOPSIS
    Install the "PrintPal" virtual Windows printer.

.DESCRIPTION
    Adds a printer that, from any app's File > Print dialog, sends the rendered
    pages into PrintPal's crop+print engine -- no saving, no file hunting.

    Two methods:

      FilePort (default, no third-party software)
        An in-box XPS driver writes each job to a file port; a small watcher
        (PrintPalPort.exe watch), registered to run at logon, grabs the file and
        hands it to "PrintPal.exe --ingest". XPS is read natively by PrintPal
        (MuPDF) -- no Ghostscript, no AGPL.

      Redirected (more robust, needs a redirection port monitor such as RedMon)
        The port pipes each job straight to "PrintPalPort.exe catch", which hands
        it to PrintPal and launches it if closed. RedMon is GPL and is NOT
        bundled -- install it yourself first. See README.md.

    Run from an elevated PowerShell (the script self-elevates if needed).

.PARAMETER Method
    FilePort (default) or Redirected.

.PARAMETER PrinterName
    Printer name shown in the Print dialog. Default: PrintPal.

.PARAMETER PrintPalExe
    Full path to PrintPal.exe. Auto-detected under Program Files / LocalAppData
    if omitted.

.PARAMETER DriverName
    Print driver to use. Default: "Microsoft XPS Document Writer v4" (in-box).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install_printer.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet("FilePort", "Redirected")] [string] $Method = "FilePort",
    [string] $PrinterName = "PrintPal",
    [string] $PrintPalExe,
    [string] $DriverName = "Microsoft XPS Document Writer v4",
    [string] $RedMonPortName = "PrintPal:"
)

$ErrorActionPreference = "Stop"

# --- self-elevate -----------------------------------------------------------
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}
if (-not (Test-Admin)) {
    Write-Host "Elevating to administrator..."
    $argList = @("-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                 "-Method", $Method, "-PrinterName", "`"$PrinterName`"",
                 "-DriverName", "`"$DriverName`"")
    if ($PrintPalExe) { $argList += @("-PrintPalExe", "`"$PrintPalExe`"") }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    return
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- locate PrintPal.exe and PrintPalPort.exe -------------------------------
function Find-PrintPal {
    param([string] $Explicit)
    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    if ($env:PRINTPAL_EXE) { $cands += $env:PRINTPAL_EXE }
    foreach ($b in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($b) { $cands += (Join-Path $b "PrintPal\PrintPal.exe") }
    }
    $cands += (Join-Path (Split-Path -Parent $here) "PrintPal.exe")
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path } }
    return $null
}

$exe = Find-PrintPal -Explicit $PrintPalExe
if (-not $exe) { throw "PrintPal.exe not found. Pass -PrintPalExe or set PRINTPAL_EXE." }
$exeDir = Split-Path -Parent $exe
$port   = Join-Path $exeDir "PrintPalPort.exe"
if (-not (Test-Path $port)) {
    Write-Warning "PrintPalPort.exe not found next to PrintPal.exe. The FilePort watcher / catcher won't run until it is present."
}
Write-Host "PrintPal.exe : $exe"

# --- ensure the driver is available -----------------------------------------
if (-not (Get-PrinterDriver -Name $DriverName -ErrorAction SilentlyContinue)) {
    Write-Host "Adding print driver '$DriverName'..."
    Add-PrinterDriver -Name $DriverName
}

$appData  = [Environment]::GetFolderPath("ApplicationData")   # per-user Roaming
$incoming = Join-Path $appData "PrintPal\spool\incoming"
New-Item -ItemType Directory -Force -Path $incoming | Out-Null

if ($Method -eq "FilePort") {
    # ---- FilePort: in-box XPS driver -> file port -> watcher -> --ingest ----
    $portFile = Join-Path $incoming "out.xps"
    if (-not (Get-PrinterPort -Name $portFile -ErrorAction SilentlyContinue)) {
        Write-Host "Creating file port: $portFile"
        Add-PrinterPort -Name $portFile
    }
    if (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue) {
        Set-Printer -Name $PrinterName -DriverName $DriverName -PortName $portFile
    } else {
        Add-Printer -Name $PrinterName -DriverName $DriverName -PortName $portFile
    }

    # Register the watcher to run at logon so prints are caught even when the
    # PrintPal window is closed.
    if (Test-Path $port) {
        $taskName = "PrintPalPortWatcher"
        $action   = New-ScheduledTaskAction -Execute $port `
                        -Argument "watch --incoming `"$incoming`" --printpal `"$exe`""
        $trigger  = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                        -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Force -RunLevel Limited | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Write-Host "Registered logon watcher task '$taskName'."
    }

    Write-Host ""
    Write-Host "Installed '$PrinterName' (FilePort)." -ForegroundColor Green
    Write-Host "NOTE: some in-box XPS drivers prompt for a filename. If yours does,"
    Write-Host "use the Redirected method (see README.md) instead."
}
elseif ($Method -eq "Redirected") {
    # ---- Redirected: redirection port monitor -> catcher -> --ingest -------
    # Requires a redirection port monitor (e.g. RedMon, GPL, user-installed).
    $monitors = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\*" `
                    -ErrorAction SilentlyContinue
    $redmon = $monitors | Where-Object { $_.PSChildName -match "Redirect|RedMon" }
    if (-not $redmon) {
        throw ("No redirection port monitor found. Install RedMon (GPL) first, " +
               "or use -Method FilePort. See winprinter/README.md.")
    }
    $monKey = "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors\$($redmon.PSChildName)\Ports\$RedMonPortName"
    Write-Host "Configuring redirection port '$RedMonPortName' -> $port catch"
    New-Item -Path $monKey -Force | Out-Null
    Set-ItemProperty -Path $monKey -Name "Command"     -Value $port
    Set-ItemProperty -Path $monKey -Name "Arguments"   -Value "catch --printpal `"$exe`""
    Set-ItemProperty -Path $monKey -Name "Printer"     -Value $PrinterName
    Set-ItemProperty -Path $monKey -Name "Output"      -Value 2   # program stdin
    Set-ItemProperty -Path $monKey -Name "ShowWindow"  -Value 0
    Set-ItemProperty -Path $monKey -Name "RunUser"     -Value 1

    if (-not (Get-PrinterPort -Name $RedMonPortName -ErrorAction SilentlyContinue)) {
        Add-PrinterPort -Name $RedMonPortName
    }
    if (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue) {
        Set-Printer -Name $PrinterName -DriverName $DriverName -PortName $RedMonPortName
    } else {
        Add-Printer -Name $PrinterName -DriverName $DriverName -PortName $RedMonPortName
    }
    Write-Host ""
    Write-Host "Installed '$PrinterName' (Redirected via $($redmon.PSChildName))." -ForegroundColor Green
}

Write-Host "Done. Try File > Print > '$PrinterName' from any app."
