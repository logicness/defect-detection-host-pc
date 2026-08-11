@echo off
rem 启动上位机（无黑框）：先清理旧缓存，再用 pythonw 启动
cd /d "%~dp0"
if exist "__pycache__" rd /s /q "__pycache__"
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
start "" pythonw main.py
