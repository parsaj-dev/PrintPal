<#
.SYNOPSIS
    Remove the "PrintPal" virtual printer and its supporting bits.

.DESCRIPTION
    Deletes the printer, its port (file port or redirection port), and the logon
    watcher scheduled task. Leaves the in-box print driver installed (other
    printers may use it). Self-elevates if needed.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\uninstall_printer.ps1
#>
[CmdletBinding()]
param(
    [string] $PrinterName = "PrintPal",
    [string] $RedMonPortName = "PrintPal:"
)

$ErrorActionPreference = "Continue"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}
if (-not (Test-Admin)) {
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-PrinterName", "`"$PrinterName`"")
    return
}

# Note the printer's port before removing it, so we can clean the right port.
$printer = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
if ($printer) {
    $portName = $printer.PortName
    Write-Host "Removing printer '$PrinterName' (port $portName)..."
    Remove-Printer -Name $PrinterName -ErrorAction SilentlyContinue
    if ($portName) {
        Remove-PrinterPort -Name $portName -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "Printer '$PrinterName' not found (already removed?)."
}

# Redirection port + its registry config, if the redirected method was used.
Remove-PrinterPort -Name $RedMonPortName -ErrorAction SilentlyContinue
$monitors = Get-ChildItem "HKLM:\SYSTEM\CurrentControlSet\Control\Print\Monitors" -ErrorAction SilentlyContinue
foreach ($m in $monitors) {
    $portKey = Join-Path $m.PSPath "Ports\$RedMonPortName"
    if (Test-Path $portKey) { Remove-Item $portKey -Recurse -Force -ErrorAction SilentlyContinue }
}

# Logon watcher task.
$task = Get-ScheduledTask -TaskName "PrintPalPortWatcher" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Removing logon watcher task..."
    Unregister-ScheduledTask -TaskName "PrintPalPortWatcher" -Confirm:$false -ErrorAction SilentlyContinue
}

Write-Host "PrintPal virtual printer removed." -ForegroundColor Green
