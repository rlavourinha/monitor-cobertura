@echo off
rem Atualiza o Monitor de Cobertura por janela e regenera output\monitor.html
rem   atualizar.bat            -> janela "diario" (padrão)
rem   atualizar.bat intraday   -> só cotações (segundos)
rem   atualizar.bat semanal    -> Focus completo
rem   atualizar.bat mensal     -> SGS de baixa frequência + realizado
rem   atualizar.bat tudo       -> primeira carga
cd /d "%~dp0"
set JANELA=%1
if "%JANELA%"=="" set JANELA=diario
python coletar.py --janela %JANELA%
python build.py
