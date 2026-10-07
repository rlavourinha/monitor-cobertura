# Agenda a janela MT5 do fim do dia (mt5_extrato.ps1): dias úteis às 18h10 (usuário atual, sem admin).
#   powershell -ExecutionPolicy Bypass -File .\agendar_mt5.ps1          # cria/atualiza
#   powershell -ExecutionPolicy Bypass -File .\agendar_mt5.ps1 -Remover # remove
param([switch]$Remover)
$nome = "Monitor MT5 extrato"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$acao = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$raiz\mt5_extrato.ps1`"" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 18:10
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 50) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
"tarefa '$nome' registrada: dias úteis às 18h10 (coletar.py --janela mt5 + push dos arquivos versionados)"
