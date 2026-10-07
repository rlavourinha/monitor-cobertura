# Janela MT5 do fim do dia (só neste PC, terminal da Genial aberto): histórico diário e de 1 min, extrato intraday
# (data/mt5_intraday.json: seletor "5 min / 1 min" dos gráficos do site) e curvas DI/DAP; depois commit + push dos
# arquivos versionados (o push dispara o build no GitHub). Agendado por agendar_mt5.ps1 (dias úteis, 18h10).
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $raiz
$log = Join-Path $raiz "data\mt5_extrato.log"
$ini = Get-Date -Format "yyyy-MM-dd HH:mm"
& python coletar.py --janela mt5 2>&1 | Out-File -FilePath $log -Encoding utf8
$arqs = @("data/mt5_intraday.json", "data/di_curva.json", "data/di_hist.json", "data/dap_curva.json", "data/dap_hist.json")
git add $arqs 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -q -m "MT5: extrato intraday e curvas DI/DAP $ini"
    git pull --rebase -X theirs -q origin main
    if ($LASTEXITCODE -ne 0) { git rebase --abort; git fetch -q origin main; git merge -q -X ours --no-edit origin/main }
    git push -q origin main
    Add-Content -Path $log -Value "$ini push rc=$LASTEXITCODE"
} else {
    Add-Content -Path $log -Value "$ini nada a publicar"
}
