# Agenda o coletor de research do Goldman Sachs: dias úteis às 08:30, 12:25 e 19:00, antes das leituras
# (08:48, 12:33, 19:04). Usuário atual, sem admin. Sessão: python probe_gringas.py --login goldman
#   powershell -ExecutionPolicy Bypass -File .\agendar_gs.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_gs.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor GS relatorios"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")   # caminho real (o alias da WindowsApps não serve ao Agendador)
$acao = New-ScheduledTaskAction -Execute $py -Argument "gs_relatorios.py" -WorkingDirectory $raiz
$dias = "Monday","Tuesday","Wednesday","Thursday","Friday"
$gatilhos = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 08:30),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 12:25),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 19:00)
)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 25) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 08:30, 12:25 e 19:00 ($py)"
