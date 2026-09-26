# Agenda o vigia em tempo real: a cada 5 min, dias úteis, 10h00–17h40 (usuário atual, sem admin).
#   powershell -ExecutionPolicy Bypass -File .\agendar_vigia.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_vigia.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor vigia tempo real"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$acao = New-ScheduledTaskAction -Execute "python" -Argument "vigia.py" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Daily -At 10:00
$gatilho.Repetition = (New-ScheduledTaskTrigger -Once -At 10:00 -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 7 -Minutes 45)).Repetition
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: a cada 5 min das 10h às 17h45 (o script sai sozinho em fim de semana e fora do pregão)"
