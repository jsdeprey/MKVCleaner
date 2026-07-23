@echo off
setlocal

cd /d "%~dp0"

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Cleaning old build files...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist MKVCleaner.spec del /q MKVCleaner.spec
if exist MKVCleaner-cli.spec del /q MKVCleaner-cli.spec

echo.
echo Building MKVCleaner.exe...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name MKVCleaner ^
  mkvcleaner.py

if errorlevel 1 goto :error

echo.
echo Building MKVCleaner-cli.exe...
python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --console ^
  --name MKVCleaner-cli ^
  mkvcleaner_cli.py

if errorlevel 1 goto :error

echo.
echo Build complete:
echo %CD%\dist\MKVCleaner.exe
echo %CD%\dist\MKVCleaner-cli.exe
echo.
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
