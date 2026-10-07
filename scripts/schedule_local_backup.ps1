# Registers a daily Windows scheduled task that backs up the production
# database to this PC (encrypted, verified, 7 daily / 4 weekly / 3 monthly).
# This is the second independent copy, next to the GitHub Actions backup.
#
# 1. Create backup\backup.env (git-ignored) containing:
#        DATABASE_URL=<Render PostgreSQL External Database URL>
#        BACKUP_PASSPHRASE=<the same passphrase as the GitHub secret>
# 2. Run:   powershell -ExecutionPolicy Bypass -File scripts\schedule_local_backup.ps1
#    Optional: -Time "21:00"  -BackupDir "D:\OneDrive\DailyPlannerBackups"
# 3. Test now:  Start-ScheduledTask -TaskName "Daily Planner database backup"
#    Log:       backup\database\backup.log (or <BackupDir>\backup.log)
# Remove later: Unregister-ScheduledTask -TaskName "Daily Planner database backup"
param(
    [string]$Time = "21:00",
    [string]$BackupDir = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }
$envFile = Join-Path $repo "backup\backup.env"
if (-not (Test-Path $envFile)) {
    Write-Error "Create $envFile first (DATABASE_URL and BACKUP_PASSPHRASE). See DATABASE.md."
}
if (-not $BackupDir) { $BackupDir = Join-Path $repo "backup\database" }
New-Item -ItemType Directory -Force $BackupDir | Out-Null
$log = Join-Path $BackupDir "backup.log"

$command = "`"$python`" scripts\backup_database.py backup --encrypt --rotate --dir `"$BackupDir`" >> `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# Runs at the next opportunity if the PC was off at the scheduled time.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "Daily Planner database backup" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Encrypted Daily Planner PostgreSQL backup (scripts/backup_database.py)" -Force | Out-Null
Write-Host "Scheduled daily at $Time. Backups go to $BackupDir"
