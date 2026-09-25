# Registra as janelas de atualização no Agendador de Tarefas do Windows (usuário atual, sem elevação).
# Rodar UMA vez, nesta pasta:  powershell -ExecutionPolicy Bypass -File .\agendar.ps1
# Remover:                     powershell -ExecutionPolicy Bypass -File .\agendar.ps1 -Remover
param([switch]$Remover)

$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat = Join-Path $pasta "atualizar.bat"
$prefixo = "MonitorCobertura"

$tarefas = @(
    @{ Nome = "$prefixo-Intraday"; Arg = "intraday"; Gatilho = { New-ScheduledTaskTrigger -Once -At "10:00" -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Hours 9) }; Descr = "Cotações com atraso, a cada hora das 10h às 19h" },
    @{ Nome = "$prefixo-Diario";   Arg = "diario";   Gatilho = { New-ScheduledTaskTrigger -Daily -At "19:30" };  Descr = "Fechamento B3 (COTAHIST do dia), Tesouro, SGS diários, consenso Yahoo" },
    @{ Nome = "$prefixo-Semanal";  Arg = "semanal";  Gatilho = { New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "08:30" }; Descr = "Focus completo (publicado segunda de manhã)" },
    @{ Nome = "$prefixo-Mensal";   Arg = "mensal";   Gatilho = { New-ScheduledTaskTrigger -Daily -At "08:00" }; Descr = "SGS mensais e realizado anual (roda diário, mas as séries só mudam no mês)" }
)

foreach ($t in $tarefas) {
    if (Get-ScheduledTask -TaskName $t.Nome -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $t.Nome -Confirm:$false }
    if ($Remover) { Write-Host "removida: $($t.Nome)"; continue }
    $acao = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`" $($t.Arg)" -WorkingDirectory $pasta
    $gat = & $t.Gatilho
    $cfg = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $t.Nome -Action $acao -Trigger $gat -Settings $cfg -Description $t.Descr | Out-Null
    Write-Host "registrada: $($t.Nome) ($($t.Arg)) — $($t.Descr)"
}
if (-not $Remover) { Write-Host "`nIntraday só faz sentido em dia útil: a janela diária/semanal ignora fim de semana sozinha (B3/Focus não publicam)." }
