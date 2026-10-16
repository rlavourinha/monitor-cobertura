# Agenda a checagem de preços do fim do dia (checa_precos.py): dias úteis às 18h15 (ref. Yahoo) e 20h30 (ref. COTAHIST oficial).
#   powershell -ExecutionPolicy Bypass -File .\agendar_checagem.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_checagem.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor checagem de precos"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = "C:\Users\rlavourinha\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$acao = New-ScheduledTaskAction -Execute $py -Argument "`"$raiz\checa_precos.py`"" -WorkingDirectory $raiz
$dias = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$gatilhos = @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 18:15), (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 20:30))
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 18h15 e 20h30"
