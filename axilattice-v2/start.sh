#!/bin/bash
# AxiLattice v2 — Quick Start Script

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║     AXILATTICE v2 — Voice-First Insight Engine              ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

if [ "$1" = "backend" ] || [ "$1" = "all" ] || [ -z "$1" ]; then
    echo "🚀 Starting single-service app (backend + frontend on one port)..."
    pip install -r backend/requirements.txt
    if [ -z "$ANTHROPIC_API_KEY" ]; then
        echo "⚠️  Warning: ANTHROPIC_API_KEY not set. NLU will use fallback regex."
    fi
    uvicorn main:app --reload --port 8000 --app-dir backend
    echo "📱 Open http://localhost:8000"
elif [ "$1" = "frontend" ]; then
    echo "🚀 Starting frontend only (static server, no backend)..."
    cd frontend/public
    python -m http.server 3000
    echo "📱 Open http://localhost:3000"
elif [ "$1" = "standalone" ]; then
    echo "🚀 Opening standalone mode..."
    open standalone/index.html  # macOS
    # xdg-open standalone/index.html  # Linux
    # start standalone/index.html  # Windows
else
    echo "Usage:"
    echo "  ./start.sh            # Start everything (backend serves frontend too)"
    echo "  ./start.sh backend    # Same as above"
    echo "  ./start.sh frontend   # Serve React SPA only (separate static server)"
    echo "  ./start.sh standalone # Open zero-backend HTML"
    echo ""
    echo "Prerequisites:"
    echo "  • Python 3.11+ with pip"
    echo "  • ANTHROPIC_API_KEY env var (for Claude NLU)"
    echo "  • Modern browser with Web Speech API support (Chrome/Edge)"
fi
