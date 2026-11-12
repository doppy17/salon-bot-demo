$p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot.main*' })
Write-Output ("экземпляров: " + $p.Count)
foreach ($x in $p) { Write-Output ("  PID " + $x.ProcessId) }
