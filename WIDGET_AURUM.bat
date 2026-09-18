@echo off
chcp 65001 >nul
title AURUM - widget
cd /d "%~dp0"

rem Widget flutuante: fica por cima do MT5. Liga o AURUM sozinho se ele estiver desligado.
rem Atalho para mostrar/esconder: Ctrl+Alt+A. Botao direito no widget: menu.
for /f "delims=" %%P in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%P"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" -m widget %*
