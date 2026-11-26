# Starts the Daily Planner desktop window (desktop.py) every time you log in to
# Windows. It pops up, then shrinks to a small button in the bottom-right
# corner; click the button to open it again.
#
# Uses a Task Scheduler "at log on" task rather than the Startup folder: the
# Startup folder waits until every other startup app has launched, which can
# take minutes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_desktop_startup.ps1
#   Remove:  powershell -ExecutionPolicy Bypass -File scripts\install_desktop_startup.ps1 -Remove
#   Log:     .data\desktop.log
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$taskName = "Daily Planner"
# Older versions of this script used a Startup-folder shortcut.
$oldShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Daily Planner.lnk"
if (Test-Path $oldShortcut) { Remove-Item $oldShortcut }

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed. Daily Planner will no longer start with Windows."
    return
}

$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw).Source }
& ($pythonw -replace "pythonw.exe$", "python.exe") -m pip install -q -r (Join-Path $repo "requirements-desktop.txt")

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$(Join-Path $repo 'desktop.py')`" --startup" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT10S"   # let the desktop and network come up first
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Open Daily Planner (desktop.py) at log on" -Force | Out-Null
Write-Host "Done. Daily Planner will open when you log in to Windows (Task Scheduler: '$taskName')."
