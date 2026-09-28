# Agenda o coletor de relatórios do Itaú BBA Smart: dias úteis às 08:25, 09:55 e 18:55 (10 min depois do BTG, para os
# dois Chromes não disputarem a máquina). Usuário atual, sem admin.
#   powershell -ExecutionPolicy Bypass -File .\agendar_smart.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_smart.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor Smart relatorios"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys; print(sys.executable)")   # caminho real (o alias da WindowsApps não serve ao Agendador)
$acao = New-ScheduledTaskAction -Execute $py -Argument "smart_relatorios.py" -WorkingDirectory $raiz
$dias = "Monday","Tuesday","Wednesday","Thursday","Friday"
$gatilhos = @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 08:25),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 09:55),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At 18:55)
)
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilhos -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 08:25, 09:55 e 18:55 ($py)"
