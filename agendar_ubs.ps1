# Agenda o coletor de research do UBS (Neo): dias úteis às 08:35, 12:28 e 18:40, antes das leituras
# (08:48, 12:33, 19:04). Usuário atual, sem admin. Sessão: python probe_gringas.py --login ubs
#   powershell -ExecutionPolicy Bypass -File .\agendar_ubs.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_ubs.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor UBS relatorios"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")   # caminho real (o alias da WindowsApps não serve ao Agendador)
$acao = New-ScheduledTaskAction -Execute $py -Argument "ubs_relatorios.py" -WorkingDirectory $raiz
$dias = "Monday","Tuesday","Wednesday","Thursday","Friday"
$gatilhos = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 08:35),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 12:28),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 18:40)
)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 08:35, 12:28 e 18:40 ($py)"
