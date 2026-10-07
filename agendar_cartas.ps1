# Agenda o vigia das cartas de gestão (cartas_check.py): todo dia às 07:50 (usuário atual, sem admin).
#   powershell -ExecutionPolicy Bypass -File .\agendar_cartas.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_cartas.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor cartas de gestao"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")
$acao = New-ScheduledTaskAction -Execute $py -Argument "cartas_check.py" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Daily -At 07:50
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: todo dia às 07:50 (avisa no Telegram quando a fonte de uma carta mudar)"
