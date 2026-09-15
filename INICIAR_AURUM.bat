@echo off
chcp 65001 >nul
title AURUM - iniciando
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python nao encontrado. Instale em https://www.python.org/downloads/ e marque "Add python.exe to PATH".
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn, yfinance, pandas, httpx" 2>nul
if errorlevel 1 (
    echo Instalando dependencias pela primeira vez...
    python -m pip install -r requirements.txt
)

rem Ja esta rodando? So abre o navegador.
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2; if ($r.app -eq 'AURUM') { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto abrir

echo Ligando o AURUM em segundo plano (sem janela)...
for /f "delims=" %%P in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%P"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0run.py" --background

powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 60; $i++) { try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2; if ($r.app -eq 'AURUM') { $ok=$true; break } } catch {}; Start-Sleep -Milliseconds 500 }; if ($ok) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo O AURUM nao respondeu. Veja o arquivo data\aurum.log para detalhes.
    pause
    exit /b 1
)

:abrir
start "" "http://localhost:8765"
echo AURUM disponivel em http://localhost:8765 (pode fechar esta janela).
timeout /t 3 >nul
