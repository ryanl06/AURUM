@echo off
chcp 65001 >nul
title AURUM - parar
powershell -NoProfile -Command "try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/api/system/shutdown' -Headers @{ 'X-AURUM' = '1' } -TimeoutSec 5 | Out-Null; Write-Host 'AURUM desligado.' } catch { Write-Host 'O AURUM ja estava desligado.' }"
timeout /t 3 >nul
