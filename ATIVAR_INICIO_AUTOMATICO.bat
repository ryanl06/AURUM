@echo off
chcp 65001 >nul
title AURUM - iniciar junto com o Windows
cd /d "%~dp0"

rem Cria um atalho na pasta Inicializar do SEU usuario (nao mexe em configuracoes do sistema).
for /f "delims=" %%P in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%P"
if not exist "%PYW%" (
    echo Nao encontrei o pythonw.exe. Confira se o Python esta instalado.
    pause
    exit /b 1
)

powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\AURUM.lnk'); $s.TargetPath = '%PYW%'; $s.Arguments = '\"%~dp0run.py\" --background'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Description = 'AURUM - analisador de mercado (http://localhost:8765)'; $s.Save()"
if errorlevel 1 (
    echo Nao foi possivel criar o atalho.
    pause
    exit /b 1
)

echo Pronto: o AURUM vai ligar sozinho sempre que voce entrar no Windows.
echo Acesse pelo navegador: http://localhost:8765  (salve nos favoritos ou instale como app).
echo Para desfazer, rode DESATIVAR_INICIO_AUTOMATICO.bat
echo.
call "%~dp0INICIAR_AURUM.bat"
