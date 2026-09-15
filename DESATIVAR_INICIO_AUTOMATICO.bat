@echo off
chcp 65001 >nul
title AURUM - desativar inicio automatico
powershell -NoProfile -Command "$p = [Environment]::GetFolderPath('Startup') + '\AURUM.lnk'; if (Test-Path $p) { Remove-Item $p; Write-Host 'Inicio automatico desativado.' } else { Write-Host 'O inicio automatico ja estava desativado.' }"
echo O AURUM continua rodando ate voce desligar o computador ou rodar PARAR_AURUM.bat.
pause
