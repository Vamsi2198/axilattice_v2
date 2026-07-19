#!/bin/bash
# AxiLattice v2 — Quick Start Script

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     AXILATTICE v2 — Voice-First Insight Engine              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

if [ "$1" = "backend" ]; then
    echo "🚀 Starting backend..."
    cd backend
    pip install -r requirements.txt
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        echo "⚠️  Warning: ANTHROPIC_API_KEY not set. NLU will use fallback regex."
    fi
    uvicorn main:app --reload --port 8000
elif [ "$1" = "frontend" ]; then
    echo "🚀 Starting frontend..."
    cd frontend/public
    python -m http.server 3000
    echo "📱 Open http://localhost:3000"
elif [ "$1" = "standalone" ]; then
    echo "🚀 Opening standalone mode..."
    if command -v open >/dev/null 2>&1; then
        open standalone/index.html
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open standalone/index.html
    else
        echo "Open standalone/index.html manually in your browser."
    fi
elif [ "$1" = "all" ]; then
    echo "🚀 Starting backend and frontend..."
    (
      cd backend || exit 1
      pip install -r requirements.txt
      if [ -z "$ANTHROPIC_API_KEY" ]; then
          echo "⚠️  Warning: ANTHROPIC_API_KEY not set. NLU will use fallback regex."
      fi
      uvicorn main:app --reload --port 8000
    ) &
    BACKEND_PID=$!

    (
      cd frontend/public || exit 1
      python -m http.server 3000
    ) &
    FRONTEND_PID=$!

    echo "Backend:  http://localhost:8000"
    echo "Frontend: http://localhost:3000"
    echo "Press Ctrl+C to stop both services."

    cleanup() {
      kill "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1
      wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    }

    trap cleanup INT TERM
    wait "$BACKEND_PID" "$FRONTEND_PID"
else
    echo "Usage:"
    echo "  ./start.sh backend    # Start FastAPI backend"
    echo "  ./start.sh frontend   # Serve React SPA"
    echo "  ./start.sh all        # Start backend + frontend"
    echo "  ./start.sh standalone # Open zero-backend HTML"
    echo ""
    echo "Prerequisites:"
    echo "  • Python 3.11+ with pip"
    echo "  • ANTHROPIC_API_KEY env var (for Claude NLU)"
    echo "  • Modern browser with Web Speech API support (Chrome/Edge)"
fi
