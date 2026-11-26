# Agenda o coletor de research do J.P. Morgan: dias úteis às 08:20, 12:15 e 18:50, antes das leituras
# (08:48, 12:33, 19:04). Usuário atual, sem admin. Sessão: python probe_gringas.py --login jpm
#   powershell -ExecutionPolicy Bypass -File .\agendar_jpm.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_jpm.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor JPM relatorios"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")   # caminho real (o alias da WindowsApps não serve ao Agendador)
$acao = New-ScheduledTaskAction -Execute $py -Argument "jpm_relatorios.py" -WorkingDirectory $raiz
$dias = "Monday","Tuesday","Wednesday","Thursday","Friday"
$gatilhos = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 08:20),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 12:15),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 18:50)
)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 08:20, 12:15 e 18:50 ($py)"
