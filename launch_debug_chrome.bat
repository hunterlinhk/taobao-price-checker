@echo off
setlocal
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo Google Chrome was not found in the standard install locations.
  pause
  exit /b 1
)
echo Starting a separate Chrome profile for browser control.
start "" "%CHROME%" --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\TaobaoPriceChrome"
if errorlevel 1 (
  echo Windows could not start Chrome.
  pause
  exit /b 1
)
echo Checking the local debugging endpoint...
for /L %%i in (1,1,15) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9222/json/version -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 goto endpoint_ready
  timeout /t 1 /nobreak >nul
)
echo Chrome did not open the debugging endpoint. Leave this window open and send me the message shown above.
pause
exit /b 1
:endpoint_ready
echo Debugging endpoint is ready. Sign into the shopping site in the new Chrome window.
echo Keep the Chrome window open. This CMD window can stay open too.
pause
endlocal
