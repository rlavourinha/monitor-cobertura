# Agenda as três leituras automáticas (leitura_headless.py) como Tarefas do Windows, dias úteis,
# usando o Claude Code CLI headless no modelo Sonnet 5 (usuário atual, sem admin).
#   powershell -ExecutionPolicy Bypass -File .\agendar_leituras.ps1           # cria/atualiza as três
#   powershell -ExecutionPolicy Bypass -File .\agendar_leituras.ps1 -Remover  # remove as três
param([switch]$Remover)

$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
$dias = @("Monday","Tuesday","Wednesday","Thursday","Friday")

# nome, argumento do leitura_headless.py, horário, tempo-limite (min)
$tarefas = @(
    @{ nome = "Monitor leitura manha"; arg = "leitura_headless.py --manha"; hora = "08:48"; limite = 40 },
    @{ nome = "Monitor leitura dia";   arg = "leitura_headless.py --dia";   hora = "19:04"; limite = 25 },
    @{ nome = "Monitor leitura extra"; arg = "leitura_headless.py --extra"; hora = "12:33"; limite = 40 }
)

if ($Remover) {
    foreach ($t in $tarefas) {
        Unregister-ScheduledTask -TaskName $t.nome -Confirm:$false -ErrorAction SilentlyContinue
        "removida: $($t.nome)"
    }
    exit
}

$py = (& python -c "import sys; print(sys.executable)")
foreach ($t in $tarefas) {
    $acao = New-ScheduledTaskAction -Execute $py -Argument $t.arg -WorkingDirectory $raiz
    $gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At $t.hora
    $cfg = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes $t.limite) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName $t.nome -Action $acao -Trigger $gatilho -Settings $cfg -Force | Out-Null
    "tarefa '$($t.nome)' registrada: dias úteis às $($t.hora) ($($t.arg))"
}
"pronto. O CLI precisa estar logado uma vez (rode 'claude' e autentique) para o modelo rodar."
