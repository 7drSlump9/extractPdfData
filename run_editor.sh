#!/bin/bash
echo ""
echo "=========================================="
echo " Template Editor - Avvio completo"
echo "=========================================="
echo ""

echo "[1/2] Avvio Flask API server (porta 8000)..."
python server.py &
FLASK_PID=$!

sleep 2

echo "[2/2] Avvio React editor (porta 3000)..."
cd web && npm run dev &
REACT_PID=$!

echo ""
echo "Browser: http://localhost:3000"
echo "Login: admin / admin"
echo ""
echo "Premi CTRL+C per fermare tutto"

trap "kill $FLASK_PID $REACT_PID 2>/dev/null; exit" INT TERM
wait