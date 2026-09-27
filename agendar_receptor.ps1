# Mantém o receptor local do BTG (btg_receptor.py, ws://127.0.0.1:8766) no ar: inicia no logon e agora, sem janela.
#   powershell -ExecutionPolicy Bypass -File .\agendar_receptor.ps1          # cria/atualiza e inicia
#   powershell -ExecutionPolicy Bypass -File .\agendar_receptor.ps1 -Remover # remove (e para)
param([switch]$Remover)
$nome = "Monitor BTG receptor"
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Remover) { Stop-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue; "removida"; exit }
$py = (& python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))")
$acao = New-ScheduledTaskAction -Execute $py -Argument "btg_receptor.py" -WorkingDirectory $raiz
$gatilho = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
Start-ScheduledTask -TaskName $nome
Start-Sleep -Seconds 3
"tarefa '$nome' registrada (logon) e iniciada: " + (Get-ScheduledTask -TaskName $nome).State
