@echo off
rem ===========================================================================
rem  Episode2 launcher - "Handwriting Notebook & PDF Annotation" (source build)
rem
rem  Usage:
rem    double-click this file      -> launch episode2 (no console window)
rem    run_episode2.cmd console    -> launch and keep a console (tracebacks)
rem    run_episode2.cmd shortcut   -> create / update the desktop shortcut
rem                                   "Episode2.lnk" pointing at this file,
rem                                   print the fields back, then pause
rem    run_episode2.cmd shortcut "D:\some dir"
rem                                -> same, but write the .lnk into that folder
rem                                   (default: the user Desktop)
rem
rem  Entry  : <this folder>\main.py
rem  Python : ..\handwriting-notebook\.venv\Scripts\python(w).exe  (shared venv)
rem
rem  NOTE: this file is intentionally ASCII-only. cmd.exe reads .cmd files with
rem  the OEM code page, so non-ASCII text in here would show up as garbage. The
rem  Chinese shortcut description is carried inside the encoded PowerShell blob
rem  of the "shortcut" branch below, which is pure ASCII on disk.
rem ===========================================================================
setlocal EnableExtensions
chcp 65001 >nul

set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"

rem -- sub-command: create / update the desktop shortcut.
if /I "%~1"=="shortcut" goto :shortcut

set "VENV=%APPDIR%\..\handwriting-notebook\.venv\Scripts"

if not exist "%APPDIR%\main.py" goto :no_main

rem -- interpreter: episode2 local venv first (if one ever appears), else the
rem    shared episode-1 venv.
set "PY="
set "PYW="
if exist "%APPDIR%\.venv\Scripts\python.exe"  set "PY=%APPDIR%\.venv\Scripts\python.exe"
if exist "%APPDIR%\.venv\Scripts\pythonw.exe" set "PYW=%APPDIR%\.venv\Scripts\pythonw.exe"
if not defined PY  if exist "%VENV%\python.exe"  set "PY=%VENV%\python.exe"
if not defined PYW if exist "%VENV%\pythonw.exe" set "PYW=%VENV%\pythonw.exe"
if not defined PY goto :no_python

rem -- default: windowless. "console" argument keeps the console for debugging.
set "RUN=%PYW%"
if /I "%~1"=="console" set "RUN=%PY%"
if not defined RUN set "RUN=%PY%"

rem -- start detached with episode2 as the working directory, then quit at once
rem    so the launcher window does not linger.
start "" /D "%APPDIR%" "%RUN%" "main.py"
if errorlevel 1 goto :start_failed
exit /b 0

rem --------------------------------------------------------------------------
rem  shortcut sub-command: WScript.Shell COM -> <dir>\Episode2.lnk
rem  It only ever writes the file "Episode2.lnk" in the chosen folder; every
rem  other .lnk (including the episode-1 one on the desktop) is left untouched.
rem  The payload is a base64/UTF-16LE blob so the Chinese description survives
rem  any console code page. pwsh is preferred, Windows PowerShell is the
rem  fallback: pwsh ships as an MSIX alias that can fail to start
rem  ("Access is denied.") while powershell.exe in System32 always starts.
rem --------------------------------------------------------------------------
:shortcut
set "HN_EP2_LNK_DIR=%~2"
set "HN_EP2_LNK_TARGET=%APPDIR%\run_episode2.cmd"
set "HN_EP2_LNK_WORKDIR=%APPDIR%"
set "HN_EP2_LNK_ICON=%APPDIR%\assets\app.ico"
if not exist "%HN_EP2_LNK_TARGET%" goto :sc_no_launcher
if not exist "%HN_EP2_LNK_ICON%" echo [Episode2] warning: icon not found: "%HN_EP2_LNK_ICON%"
set "PSARGS=-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand JABFAHIAcgBvAHIAQQBjAHQAaQBvAG4AUAByAGUAZgBlAHIAZQBuAGMAZQAgAD0AIAAnAFMAdABvAHAAJwANAAoAJABsAG4AawAgAD0AIAAnACcADQAKAHQAcgB5ACAAewANAAoAIAAgACQAZABpAHIAIAA9ACAAJABlAG4AdgA6AEgATgBfAEUAUAAyAF8ATABOAEsAXwBEAEkAUgANAAoAIAAgAGkAZgAgACgAWwBzAHQAcgBpAG4AZwBdADoAOgBJAHMATgB1AGwAbABPAHIAVwBoAGkAdABlAFMAcABhAGMAZQAoACQAZABpAHIAKQApACAAewAgACQAZABpAHIAIAA9ACAAWwBFAG4AdgBpAHIAbwBuAG0AZQBuAHQAXQA6ADoARwBlAHQARgBvAGwAZABlAHIAUABhAHQAaAAoACcARABlAHMAawB0AG8AcAAnACkAIAB9AA0ACgAgACAAJABsAG4AawAgAD0AIABKAG8AaQBuAC0AUABhAHQAaAAgACQAZABpAHIAIAAnAEUAcABpAHMAbwBkAGUAMgAuAGwAbgBrACcADQAKACAAIAAkAHQAYQByAGcAZQB0ACAAIAA9ACAAJABlAG4AdgA6AEgATgBfAEUAUAAyAF8ATABOAEsAXwBUAEEAUgBHAEUAVAANAAoAIAAgACQAdwBvAHIAawBkAGkAcgAgAD0AIAAkAGUAbgB2ADoASABOAF8ARQBQADIAXwBMAE4ASwBfAFcATwBSAEsARABJAFIADQAKACAAIAAkAGkAYwBvAG4AIAAgACAAIAA9ACAAJABlAG4AdgA6AEgATgBfAEUAUAAyAF8ATABOAEsAXwBJAEMATwBOAA0ACgAgACAAJABkAGUAcwBjACAAIAAgACAAPQAgACcAS2KZURR7sIssZw5OUABEAEYAeWLobCAARQBwAGkAcwBvAGQAZQAyAAj/kG4BeEhyCf8nAA0ACgAgACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcAJwANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAoACcARQBwAGkAcwBvAGQAZQAyACAAcwBoAG8AcgB0AGMAdQB0ACAALQA+ACAAJwAgACsAIAAkAGwAbgBrACkADQAKACAAIABpAGYAIAAoAC0AbgBvAHQAIAAoAFQAZQBzAHQALQBQAGEAdABoACAALQBMAGkAdABlAHIAYQBsAFAAYQB0AGgAIAAkAGQAaQByACkAKQAgAHsAIAB0AGgAcgBvAHcAIAAoACcAbwB1AHQAcAB1AHQAIABmAG8AbABkAGUAcgAgAGQAbwBlAHMAIABuAG8AdAAgAGUAeABpAHMAdAA6ACAAJwAgACsAIAAkAGQAaQByACkAIAB9AA0ACgAgACAAaQBmACAAKABUAGUAcwB0AC0AUABhAHQAaAAgAC0ATABpAHQAZQByAGEAbABQAGEAdABoACAAJABsAG4AawApACAAewAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACAAIABuAG8AdABlADoAIABFAHAAaQBzAG8AZABlADIALgBsAG4AawAgAGEAbAByAGUAYQBkAHkAIABlAHgAaQBzAHQAcwAsACAAdQBwAGQAYQB0AGkAbgBnACAAdABoAGEAdAAgAG8AbgBlACAAZgBpAGwAZQAgAG8AbgBsAHkAJwAgAH0ADQAKACAAIAAkAHMAaAAgAD0AIABOAGUAdwAtAE8AYgBqAGUAYwB0ACAALQBDAG8AbQBPAGIAagBlAGMAdAAgAFcAUwBjAHIAaQBwAHQALgBTAGgAZQBsAGwADQAKACAAIAAkAGwAIAA9ACAAJABzAGgALgBDAHIAZQBhAHQAZQBTAGgAbwByAHQAYwB1AHQAKAAkAGwAbgBrACkADQAKACAAIAAkAGwALgBUAGEAcgBnAGUAdABQAGEAdABoACAAPQAgACQAdABhAHIAZwBlAHQADQAKACAAIAAkAGwALgBXAG8AcgBrAGkAbgBnAEQAaQByAGUAYwB0AG8AcgB5ACAAPQAgACQAdwBvAHIAawBkAGkAcgANAAoAIAAgACQAbAAuAEkAYwBvAG4ATABvAGMAYQB0AGkAbwBuACAAPQAgACQAaQBjAG8AbgAgACsAIAAnACwAMAAnAA0ACgAgACAAJABsAC4ARABlAHMAYwByAGkAcAB0AGkAbwBuACAAPQAgACQAZABlAHMAYwANAAoAIAAgACQAbAAuAFMAYQB2AGUAKAApAA0ACgAgACAAJAB2ACAAPQAgACQAcwBoAC4AQwByAGUAYQB0AGUAUwBoAG8AcgB0AGMAdQB0ACgAJABsAG4AawApAA0ACgAgACAAVwByAGkAdABlAC0ASABvAHMAdAAgACgAJwAgACAAVABhAHIAZwBlAHQAUABhAHQAaAAgACAAIAAgACAAIAAgAD0AIAAnACAAKwAgACQAdgAuAFQAYQByAGcAZQB0AFAAYQB0AGgAKQANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAoACcAIAAgAFcAbwByAGsAaQBuAGcARABpAHIAZQBjAHQAbwByAHkAIAA9ACAAJwAgACsAIAAkAHYALgBXAG8AcgBrAGkAbgBnAEQAaQByAGUAYwB0AG8AcgB5ACkADQAKACAAIABXAHIAaQB0AGUALQBIAG8AcwB0ACAAKAAnACAAIABJAGMAbwBuAEwAbwBjAGEAdABpAG8AbgAgACAAIAAgACAAPQAgACcAIAArACAAJAB2AC4ASQBjAG8AbgBMAG8AYwBhAHQAaQBvAG4AKQANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAoACcAIAAgAEQAZQBzAGMAcgBpAHAAdABpAG8AbgAgAHMAZQB0ACAAIAA9ACAAJwAgACsAIAAoACQAdgAuAEQAZQBzAGMAcgBpAHAAdABpAG8AbgAgAC0AYwBlAHEAIAAkAGQAZQBzAGMAKQApAA0ACgAgACAAVwByAGkAdABlAC0ASABvAHMAdAAgACgAJwAgACAAVABhAHIAZwBlAHQARQB4AGkAcwB0AHMAIAAgACAAIAAgAD0AIAAnACAAKwAgACgAVABlAHMAdAAtAFAAYQB0AGgAIAAtAEwAaQB0AGUAcgBhAGwAUABhAHQAaAAgACQAdgAuAFQAYQByAGcAZQB0AFAAYQB0AGgAKQApAA0ACgAgACAAVwByAGkAdABlAC0ASABvAHMAdAAgACcAJwANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnAE8ASwA6ACAAcwBoAG8AcgB0AGMAdQB0ACAAYwByAGUAYQB0AGUAZAAuACAAWQBvAHUAIABjAGEAbgAgAGMAbABvAHMAZQAgAHQAaABpAHMAIAB3AGkAbgBkAG8AdwAgAG4AbwB3AC4AJwANAAoAIAAgAGUAeABpAHQAIAAwAA0ACgB9ACAAYwBhAHQAYwBoACAAewANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAnACcADQAKACAAIABXAHIAaQB0AGUALQBIAG8AcwB0ACAAJwBFAFIAUgBPAFIAOgAgAGMAbwB1AGwAZAAgAG4AbwB0ACAAYwByAGUAYQB0AGUAIAB0AGgAZQAgAHMAaABvAHIAdABjAHUAdAAuACcADQAKACAAIABXAHIAaQB0AGUALQBIAG8AcwB0ACAAKAAnACAAIABFAHgAYwBlAHAAdABpAG8AbgAgADoAIAAnACAAKwAgACQAXwAuAEUAeABjAGUAcAB0AGkAbwBuAC4ARwBlAHQAVAB5AHAAZQAoACkALgBGAHUAbABsAE4AYQBtAGUAKQANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAoACcAIAAgAEgAUgBFAFMAVQBMAFQAIAAgACAAOgAgADAAeAAnACAAKwAgACgAJwB7ADAAOgBYADgAfQAnACAALQBmACAAJABfAC4ARQB4AGMAZQBwAHQAaQBvAG4ALgBIAFIAZQBzAHUAbAB0ACkAKQANAAoAIAAgAFcAcgBpAHQAZQAtAEgAbwBzAHQAIAAoACcAIAAgAE0AZQBzAHMAYQBnAGUAIAAgACAAOgAgACcAIAArACAAJABfAC4ARQB4AGMAZQBwAHQAaQBvAG4ALgBNAGUAcwBzAGEAZwBlACkADQAKACAAIABXAHIAaQB0AGUALQBIAG8AcwB0ACAAKAAnACAAIABQAGEAdABoACAAIAAgACAAIAAgADoAIAAnACAAKwAgACQAbABuAGsAKQANAAoAIAAgAGUAeABpAHQAIAAzAA0ACgB9AA=="
set "PSEXE=powershell"
where pwsh >nul 2>nul && set "PSEXE=pwsh"
"%PSEXE%" %PSARGS%
if not errorlevel 1 goto :sc_done
if "%ERRORLEVEL%"=="3" goto :sc_failed
if /I "%PSEXE%"=="powershell" goto :sc_failed
echo [Episode2] "%PSEXE%" could not run (exit %ERRORLEVEL%), retrying with Windows PowerShell...
powershell %PSARGS%
if not errorlevel 1 goto :sc_done
goto :sc_failed

:sc_done
echo.
pause
exit /b 0

:sc_no_launcher
echo [Episode2] Cannot find this launcher itself:
echo            "%HN_EP2_LNK_TARGET%"
pause
exit /b 1

:sc_failed
echo.
echo [Episode2] The shortcut was NOT created (see the error above).
echo [Episode2] Nothing else on the desktop was touched.
pause
exit /b 1

rem --------------------------------------------------------------------------
:no_main
echo [Episode2] main.py not found next to this launcher:
echo            "%APPDIR%\main.py"
echo [Episode2] The episode2 folder looks incomplete.
pause
exit /b 1

:no_python
echo [Episode2] Python interpreter not found. Looked for:
echo            "%APPDIR%\.venv\Scripts\python.exe"
echo            "%VENV%\python.exe"
echo [Episode2] Expected the shared venv of episode 1 (handwriting-notebook\.venv).
pause
exit /b 1

:start_failed
echo [Episode2] Failed to launch "%RUN%" with "%APPDIR%\main.py".
echo [Episode2] Re-run as:   run_episode2.cmd console
pause
exit /b 1
