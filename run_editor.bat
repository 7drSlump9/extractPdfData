@echo off
echo.
echo ==========================================
echo  Template Editor - Avvio completo
echo ==========================================
echo.

echo [1/2] Avvio Flask API server (porta 8000)...
start "Flask API" cmd /c "python server.py"

echo [2/2] Avvio React editor (porta 3000)...
cd web
call npm run dev

echo.
echo Chrome/Edge: http://localhost:3000
echo Login: admin / admin
pause