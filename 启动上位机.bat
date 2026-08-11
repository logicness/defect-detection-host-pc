@echo off
rem 启动上位机（无黑框）：优先使用 Anaconda 环境（含 PyQt5），避免 PATH 中无依赖的 pythonw 静默闪退
cd /d "%~dp0"
if exist "__pycache__" rd /s /q "__pycache__"
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

set "PYW=E:\Anaconda\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" main.py
