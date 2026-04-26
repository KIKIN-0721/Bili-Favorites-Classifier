@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "VENV_DIR=%ROOT_DIR%.venv-build"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo [1/4] Creating build virtual environment...
  python -m venv "%VENV_DIR%"
)

echo [2/4] Installing build dependencies...
call "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
call "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%ROOT_DIR%requirements.txt" pyinstaller
if errorlevel 1 goto :fail

echo [3/4] Building exe with PyInstaller...
call "%VENV_DIR%\Scripts\pyinstaller.exe" --noconfirm --clean "%ROOT_DIR%BiliFavoritesClassifier.spec"
if errorlevel 1 goto :fail

echo [4/4] Build completed.
echo Output: "%ROOT_DIR%dist\BiliFavoritesClassifier.exe"
goto :eof

:fail
echo Build failed.
exit /b 1
