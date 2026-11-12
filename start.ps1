$ErrorActionPreference = "Stop"
$dir = "D:\freelance\portfolio\salon-bot"
$py  = "D:\freelance\.venv\Scripts\python.exe"

# 1. гасим всё, что уже запущено, чтобы не было конфликта getUpdates
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot.main*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Seconds 2

# 2. запускаем один экземпляр в фоне (лог пишет сам бот в bot.log)
Start-Process -FilePath $py -ArgumentList '-m','bot.main' -WorkingDirectory $dir -WindowStyle Hidden

Start-Sleep -Seconds 5

# 3. проверяем, что процесс есть
$procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot.main*' })
Write-Output ("живых экземпляров: " + $procs.Count)
Write-Output "--- лог:"
Get-Content "$dir\bot.log" -Tail 4 -ErrorAction SilentlyContinue
