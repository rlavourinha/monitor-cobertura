# Agenda o coletor de relatórios do BTG: dias úteis às 07:40, 12:40 e 18:40 (usuário atual, sem admin).
#   powershell -ExecutionPolicy Bypass -File .\agendar_btg.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_btg.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor BTG relatorios"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")   # caminho real (o alias da WindowsApps não serve ao Agendador)
$acao = New-ScheduledTaskAction -Execute $py -Argument "btg_relatorios.py" -WorkingDirectory $raiz
$dias = "Monday","Tuesday","Wednesday","Thursday","Friday"
$gatilhos = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 07:40),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 12:40),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 18:40)
)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 07:40, 12:40 e 18:40 ($py)"
