Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot.main*' } |
    Select-Object ProcessId, ParentProcessId, CreationDate, CommandLine |
    Format-List
