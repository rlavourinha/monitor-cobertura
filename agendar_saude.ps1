# Agenda o vigia das rotinas (saude_rotinas.py): todo dia, de hora em hora das 07:20 às 23:20.
# Avisa no Telegram quando uma coleta NÃO RODOU (BBA, Bradesco, BTG, checagem, cartas, vigia, GitHub) ou a sessão do Itaú BBA caiu.
#   powershell -ExecutionPolicy Bypass -File .\agendar_saude.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_saude.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor saude das rotinas"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = "C:\Users\rlavourinha\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$acao = New-ScheduledTaskAction -Execute $py -Argument "`"$raiz\saude_rotinas.py`"" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Daily -At 07:20
$gatilho.Repetition = (New-ScheduledTaskTrigger -Once -At 07:20 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 16)).Repetition
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: todo dia, de hora em hora das 07:20 às 23:20"
