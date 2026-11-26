# Agenda o mantenedor de sessões dos portais de research (hoje o UBS Neo, que cai após ~1h30 sem uso): a cada 45 min,
# todos os dias. Usuário atual, sem admin. Só roda com o computador ligado.
#   powershell -ExecutionPolicy Bypass -File .\agendar_sessoes.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_sessoes.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor manter sessoes research"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")
$acao = New-ScheduledTaskAction -Execute $py -Argument "manter_sessoes.py" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 45) -RepetitionDuration (New-TimeSpan -Days 3650)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: a cada 45 min ($py)"
