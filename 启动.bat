@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 找工作助手

where python >nul 2>nul
if %errorlevel%==0 (
  python start.py
  goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py
  goto :eof
)

echo.
echo   没有检测到 Python，程序没法启动。
echo   请让技术人员在这台电脑上安装 Python 3.9 以上版本
echo   （安装时记得勾选 Add Python to PATH），然后再双击本文件。
echo.
pause
