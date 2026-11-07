# =====================================================================
#  register_startup_tasks.ps1
#  Registers the translator service and n8n as Windows Scheduled Tasks
#  that start automatically when you log in. NO ADMIN RIGHTS REQUIRED.
#
#  Run once, from PowerShell, inside the automation\ folder:
#      powershell -ExecutionPolicy Bypass -File .\register_startup_tasks.ps1
#
#  To undo:  Unregister-ScheduledTask -TaskName "Mutarjim*" -Confirm:$false
# =====================================================================

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$tasks = @(
    @{ Name = "MutarjimTranslator"; Bat = Join-Path $here "run_translator.bat" },
    @{ Name = "MutarjimN8n";        Bat = Join-Path $here "run_n8n.bat" }
)

foreach ($t in $tasks) {
    if (-not (Test-Path $t.Bat)) { throw "Missing script: $($t.Bat)" }

    # Run the .bat minimized via cmd.exe
    $action  = New-ScheduledTaskAction -Execute "cmd.exe" `
                 -Argument "/c `"$($t.Bat)`""
    # Fire at user logon
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    # Keep running indefinitely, restart on failure
    $settings = New-ScheduledTaskSettingsSet `
                 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                 -ExecutionTimeLimit (New-TimeSpan -Hours 0)   # 0 = no limit

    # Replace any existing task of the same name
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask -TaskName $t.Name -Action $action `
        -Trigger $trigger -Settings $settings `
        -Description "Mutarjim unattended legal translation ($($t.Name))" | Out-Null

    Write-Host "Registered scheduled task: $($t.Name)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Both services will start at your next login." -ForegroundColor Cyan
Write-Host "To start them right now without logging out, run:" -ForegroundColor Cyan
Write-Host "    Start-ScheduledTask -TaskName MutarjimTranslator"
Write-Host "    Start-ScheduledTask -TaskName MutarjimN8n"
