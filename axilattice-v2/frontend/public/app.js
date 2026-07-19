/* AxiLattice Frontend v2 — Voice-First Insight Dashboard */
const { useState, useEffect, useRef, useCallback } = React;

const API_BASE = window.location.hostname === 'localhost' 
  ? 'http://localhost:8000' 
  : (window.REACT_APP_API_URL || window.location.origin);

/* ─── Icons ─────────────────────────────────────────────────────────────── */
const MicIcon = () => <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" /></svg>;
const SendIcon = () => <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" /></svg>;
const UploadIcon = () => <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" /></svg>;
const ChartIcon = () => <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>;
const PlusIcon = () => <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" /></svg>;
const TrashIcon = () => <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>;
const AlertIcon = () => <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" /></svg>;
const VoiceWave = ({ active }) => (
  <div className={`flex items-center gap-0.5 h-6 ${active ? 'opacity-100' : 'opacity-30'}`}>
    {[1,2,3,4,5].map(i => (
      <div key={i} className={`w-1 rounded-full bg-axl-500 transition-all duration-150 ${active ? 'animate-pulse' : ''}`}
        style={{height: active ? `${Math.random()*20+4}px` : '4px', animationDelay: `${i*100}ms`}} />
    ))}
  </div>
);

/* ─── Chart Renderer ────────────────────────────────────────────────────── */
function InsightChart({ card, compact }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current || !card?.chart_data?.length) return;
    if (chartRef.current) { chartRef.current.destroy(); }
    const ctx = canvasRef.current.getContext('2d');
    const type = card.chart_type;
    const data = card.chart_data;

    let config;
    if (type === 'kpi') {
      return; // No chart for pure KPI
    }

    const labels = data.map(d => d.label || d.period);
    const values = data.map(d => d.value);
    const color = card.delta > 0 ? '#10b981' : card.delta < 0 ? '#ef4444' : '#0ea5e9';

    if (type === 'pie') {
      config = {
        type: 'doughnut',
        data: {
          labels,
          datasets: [{
            data: values,
            backgroundColor: ['#0ea5e9','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#06b6d4'],
            borderWidth: 0
          }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } } }
      };
    } else if (type === 'area') {
      config = {
        type: 'line',
        data: { labels, datasets: [{
          data: values,
          borderColor: color,
          backgroundColor: color + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: color
        }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: '#f4f4f5' } }, x: { grid: { display: false } } }, plugins: { legend: { display: false } } }
      };
    } else {
      config = {
        type: 'bar',
        data: { labels, datasets: [{
          data: values,
          backgroundColor: color + '90',
          borderRadius: 4,
          barThickness: compact ? 16 : 24
        }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: '#f4f4f5' } }, x: { grid: { display: false } } }, plugins: { legend: { display: false } } }
      };
    }
    chartRef.current = new Chart(ctx, config);
    return () => { if (chartRef.current) chartRef.current.destroy(); };
  }, [card]);

  if (card.chart_type === 'kpi') {
    const fmt = v => {
      if (v == null) return '—';
      if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(1) + 'M';
      if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1) + 'K';
      return v.toFixed(2);
    };
    return (
      <div className="flex flex-col items-center justify-center h-full">
        <div className="text-4xl font-bold text-surface-900">{fmt(card.kpi)}</div>
        {card.delta != null && (
          <div className={`text-sm font-medium mt-1 ${card.delta > 0 ? 'text-emerald-600' : 'text-red-500'}`}>
            {card.delta > 0 ? '▲' : '▼'} {Math.abs(card.delta*100).toFixed(1)}%
          </div>
        )}
        <div className="text-xs text-surface-300 mt-1">{card.period}</div>
      </div>
    );
  }

  return <canvas ref={canvasRef} className="w-full h-full" />;
}

/* ─── Insight Card ──────────────────────────────────────────────────────── */
function InsightCard({ card, onAddToDashboard, onAlert, compact }) {
  const [expanded, setExpanded] = useState(false);
  if (!card) return null;

  return (
    <div className={`bg-white rounded-xl border border-surface-200 shadow-sm hover:shadow-md transition-shadow ${compact ? 'p-3' : 'p-4'}`}>
      <div className="flex items-start justify-between mb-3">
        <div className="min-w-0">
          <h3 className={`font-semibold text-surface-900 truncate ${compact ? 'text-sm' : 'text-base'}`}>{card.title}</h3>
          <p className="text-xs text-surface-300 mt-0.5">{card.measure} • {card.grain}{card.period ? ` • ${card.period}` : ''}</p>
        </div>
        <div className="flex gap-1 ml-2 shrink-0">
          <button onClick={() => onAddToDashboard?.(card)} className="p-1.5 rounded-lg hover:bg-axl-50 text-axl-600 transition-colors" title="Add to dashboard">
            <PlusIcon />
          </button>
          <button onClick={() => onAlert?.(card)} className="p-1.5 rounded-lg hover:bg-amber-50 text-amber-600 transition-colors" title="Set alert">
            <AlertIcon />
          </button>
        </div>
      </div>

      <div className={`${compact ? 'h-32' : 'h-48'}`}>
        <InsightChart card={card} compact={compact} />
      </div>

      {card.summary && (
        <p className={`text-surface-400 mt-3 ${compact ? 'text-xs line-clamp-2' : 'text-sm'}`}>{card.summary}</p>
      )}

      {card.voice_suggestions && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {card.voice_suggestions.slice(0, 3).map((s, i) => (
            <span key={i} className="text-xs px-2 py-1 bg-axl-50 text-axl-700 rounded-full cursor-pointer hover:bg-axl-100 transition-colors"
              onClick={() => window.dispatchEvent(new CustomEvent('axl-query', {detail: s}))}>
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Dashboard Builder ───────────────────────────────────────────────────── */
function DashboardBuilder({ cards, onRemove, onSave, onLayoutChange, layout }) {
  const [name, setName] = useState('My Dashboard');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/dashboard`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, layout, cards, session_id: localStorage.getItem('axl_session') || 'default' })
      });
      const data = await res.json();
      alert(`Dashboard saved! ID: ${data.id}`);
    } catch (e) { alert('Save failed: ' + e.message); }
    setSaving(false);
  };

  const gridCols = layout === 'grid' ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3' : 
                   layout === 'wide' ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1';

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 bg-white p-3 rounded-lg border border-surface-200">
        <input value={name} onChange={e => setName(e.target.value)} 
          className="flex-1 px-3 py-2 rounded-lg border border-surface-200 text-sm focus:outline-none focus:ring-2 focus:ring-axl-400" />
        <select value={layout} onChange={e => onLayoutChange(e.target.value)}
          className="px-3 py-2 rounded-lg border border-surface-200 text-sm bg-white">
          <option value="grid">Grid (3-col)</option>
          <option value="wide">Wide (2-col)</option>
          <option value="single">Single Column</option>
        </select>
        <button onClick={handleSave} disabled={saving || cards.length === 0}
          className="px-4 py-2 bg-axl-600 text-white rounded-lg text-sm font-medium hover:bg-axl-700 disabled:opacity-50 transition-colors">
          {saving ? 'Saving...' : 'Save Dashboard'}
        </button>
      </div>

      {cards.length === 0 ? (
        <div className="text-center py-20 text-surface-300">
          <ChartIcon />
          <p className="mt-2 text-sm">Add insights from queries to build your dashboard</p>
        </div>
      ) : (
        <div className={`grid ${gridCols} gap-4`}>
          {cards.map((card, i) => (
            <div key={card.id || i} className="relative group">
              <InsightCard card={card} compact={layout === 'grid'} />
              <button onClick={() => onRemove(i)} 
                className="absolute top-2 right-2 p-1.5 rounded-lg bg-red-50 text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                <TrashIcon />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── Voice Input Component ──────────────────────────────────────────────── */
function VoiceInput({ onTranscript, onSubmit, disabled }) {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const recognitionRef = useRef(null);

  useEffect(() => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognitionRef.current = new SR();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = true;
      recognitionRef.current.onresult = (e) => {
        const text = Array.from(e.results).map(r => r[0].transcript).join('');
        setTranscript(text);
        onTranscript?.(text);
      };
      recognitionRef.current.onend = () => setListening(false);
      recognitionRef.current.onerror = () => setListening(false);
    }
  }, []);

  const toggleListen = () => {
    if (!recognitionRef.current) {
      alert('Speech recognition not supported in this browser. Use Chrome or Edge.');
      return;
    }
    if (listening) {
      recognitionRef.current.stop();
      setListening(false);
    } else {
      setTranscript('');
      recognitionRef.current.start();
      setListening(true);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (transcript.trim()) { onSubmit(transcript.trim()); setTranscript(''); }
  };

  return (
    <form onSubmit={handleSubmit} className="relative">
      <div className="flex items-center gap-2 bg-white border border-surface-200 rounded-2xl px-4 py-3 shadow-sm focus-within:ring-2 focus-within:ring-axl-400 focus-within:border-axl-400 transition-all">
        <button type="button" onClick={toggleListen}
          className={`p-2 rounded-xl transition-all ${listening ? 'bg-red-50 text-red-500 animate-pulse' : 'hover:bg-surface-100 text-surface-400'}`}>
          <MicIcon />
        </button>
        <VoiceWave active={listening} />
        <input 
          type="text" 
          value={transcript} 
          onChange={e => { setTranscript(e.target.value); onTranscript?.(e.target.value); }}
          placeholder={listening ? 'Listening... speak now' : 'Ask anything about your data...'}
          disabled={disabled}
          className="flex-1 bg-transparent outline-none text-sm placeholder:text-surface-300"
        />
        <button type="submit" disabled={!transcript.trim() || disabled}
          className="p-2 rounded-xl bg-axl-600 text-white hover:bg-axl-700 disabled:opacity-40 transition-colors">
          <SendIcon />
        </button>
      </div>
    </form>
  );
}

/* ─── Alert Modal ─────────────────────────────────────────────────────────── */
function AlertModal({ card, onClose, onCreate, sessionId }) {
  const [threshold, setThreshold] = useState(card.kpi || 0);
  const [direction, setDirection] = useState('above');
  const [name, setName] = useState(`${card.measure} alert`);
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const res = await fetch(`${API_BASE}/alerts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, measure: card.measure, grain: card.grain,
          threshold: Number(threshold), direction,
          dimension: card.dimension, dim_value: card.chart_data?.[0]?.label,
          session_id: sessionId
        })
      });
      await res.json();
      onClose();
    } catch (e) { alert('Failed: ' + e.message); }
    setCreating(false);
  };

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl p-6 w-full max-w-md shadow-xl">
        <h3 className="text-lg font-semibold mb-4">Set Alert</h3>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-surface-400 uppercase">Name</label>
            <input value={name} onChange={e => setName(e.target.value)} className="w-full mt-1 px-3 py-2 rounded-lg border border-surface-200 text-sm" />
          </div>
          <div>
            <label className="text-xs font-medium text-surface-400 uppercase">Trigger when {card.measure} is</label>
            <div className="flex gap-2 mt-1">
              <button onClick={() => setDirection('above')} className={`flex-1 py-2 rounded-lg text-sm font-medium border ${direction==='above' ? 'bg-axl-600 text-white border-axl-600' : 'bg-white text-surface-600 border-surface-200'}`}>Above</button>
              <button onClick={() => setDirection('below')} className={`flex-1 py-2 rounded-lg text-sm font-medium border ${direction==='below' ? 'bg-axl-600 text-white border-axl-600' : 'bg-white text-surface-600 border-surface-200'}`}>Below</button>
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-surface-400 uppercase">Threshold</label>
            <input type="number" value={threshold} onChange={e => setThreshold(e.target.value)} 
              className="w-full mt-1 px-3 py-2 rounded-lg border border-surface-200 text-sm" />
          </div>
        </div>
        <div className="flex gap-2 mt-6">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-surface-200 text-sm font-medium hover:bg-surface-50">Cancel</button>
          <button onClick={handleCreate} disabled={creating} className="flex-1 py-2.5 rounded-lg bg-axl-600 text-white text-sm font-medium hover:bg-axl-700 disabled:opacity-50">
            {creating ? 'Creating...' : 'Create Alert'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Main App ───────────────────────────────────────────────────────────── */
function App() {
  const [sessionId, setSessionId] = useState(() => {
    let sid = localStorage.getItem('axl_session');
    if (!sid) { sid = Math.random().toString(36).slice(2, 14); localStorage.setItem('axl_session', sid); }
    return sid;
  });
  const [buildStatus, setBuildStatus] = useState('idle');
  const [schema, setSchema] = useState(null);
  const [cards, setCards] = useState([]);
  const [dashboardCards, setDashboardCards] = useState([]);
  const [layout, setLayout] = useState('grid');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('chat');
  const [alertCard, setAlertCard] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const fileInputRef = useRef(null);

  // Hydrate session state on page load/reload so query input reflects backend reality.
  useEffect(() => {
    let cancelled = false;
    const loadSchema = async () => {
      try {
        const res = await fetch(`${API_BASE}/schema?session_id=${sessionId}`);
        if (!res.ok) {
          if (res.status === 404 && !cancelled) {
            setBuildStatus('idle');
          }
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        if (data?.build_status) {
          setBuildStatus(data.build_status);
        }
        if (data?.schema) setSchema(data.schema);
        if (data?.build_status === 'error') {
          setError(data.build_error || 'Cube build failed. Please upload again.');
        }
      } catch (e) {
        // Ignore bootstrap errors; user can still upload a new file.
      }
    };
    loadSchema();
    return () => { cancelled = true; };
  }, [sessionId]);

  // Poll build status
  useEffect(() => {
    if (buildStatus !== 'building') return;
    const iv = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/schema?session_id=${sessionId}`);
        if (!res.ok) {
          // Keep polling while build is in progress; transient errors should not reset UI state.
          return;
        }
        const data = await res.json();
        if (!data?.build_status) {
          return;
        }
        setBuildStatus(data.build_status);
        if (data.build_status === 'ready') { setSchema(data.schema); clearInterval(iv); }
        if (data.build_status === 'error') {
          setError(data.build_error || 'Cube build failed');
          clearInterval(iv);
        }
      } catch (e) { /* ignore */ }
    }, 2000);
    return () => clearInterval(iv);
  }, [buildStatus, sessionId]);

  // Load suggestions when schema ready
  useEffect(() => {
    if (buildStatus !== 'ready') return;
    fetch(`${API_BASE}/suggest?session_id=${sessionId}`)
      .then(r => r.json()).then(d => setSuggestions(d.suggestions || []));
  }, [buildStatus, sessionId]);

  // Listen for query events from suggestion chips
  useEffect(() => {
    const handler = (e) => handleQuery(e.detail);
    window.addEventListener('axl-query', handler);
    return () => window.removeEventListener('axl-query', handler);
  }, [sessionId, buildStatus]);

  const handleUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setBuildStatus('building');
    setError(null);
    setCards([]);
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`${API_BASE}/upload?session_id=${encodeURIComponent(sessionId)}`, { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Upload failed');
      if (data.session_id) {
        setSessionId(data.session_id);
        localStorage.setItem('axl_session', data.session_id);
      }
      if (data.status) {
        setBuildStatus(data.status);
      }
      setSchema(data.schema);
    } catch (e) { setError(e.message); setBuildStatus('idle'); }
  };

  const handleQuery = async (text) => {
    if (!text.trim()) return;
    if (buildStatus !== 'ready') {
      setError('Cube is not ready yet. Upload data and wait for the status to become Live.');
      return;
    }
    setLoading(true); setError(null);
    try {
      const res = await fetch(`${API_BASE}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: sessionId })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Query failed');
      setCards(prev => [data, ...prev].slice(0, 20));
    } catch (e) { setError(e.message); }
    setLoading(false);
  };

  const addToDashboard = (card) => {
    setDashboardCards(prev => [...prev, card]);
    setActiveTab('dashboard');
  };

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-surface-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-axl-600 flex items-center justify-center">
              <span className="text-white font-bold text-sm">A</span>
            </div>
            <h1 className="font-semibold text-surface-900">AxiLattice <span className="text-axl-600">v2</span></h1>
            <span className={`text-xs px-2 py-0.5 rounded-full ${buildStatus==='ready' ? 'bg-emerald-50 text-emerald-600' : buildStatus==='building' ? 'bg-amber-50 text-amber-600' : 'bg-surface-100 text-surface-400'}`}>
              {buildStatus === 'ready' ? '● Live' : buildStatus === 'building' ? '◌ Building...' : '○ Idle'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <input type="file" ref={fileInputRef} onChange={handleUpload} accept=".csv,.xlsx,.parquet" className="hidden" />
            <button onClick={() => fileInputRef.current?.click()} 
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-surface-200 text-sm font-medium hover:bg-surface-50 transition-colors">
              <UploadIcon /> Upload Data
            </button>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="bg-white border-b border-surface-200">
        <div className="max-w-7xl mx-auto px-4 flex gap-6">
          {['chat','dashboard'].map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)}
              className={`py-3 text-sm font-medium border-b-2 transition-colors ${activeTab===tab ? 'border-axl-600 text-axl-700' : 'border-transparent text-surface-400 hover:text-surface-600'}`}>
              {tab === 'chat' ? 'Insight Chat' : `Dashboard (${dashboardCards.length})`}
            </button>
          ))}
        </div>
      </div>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6">
        {activeTab === 'chat' ? (
          <div className="space-y-6">
            {/* Input */}
            <VoiceInput 
              onSubmit={handleQuery} 
              disabled={buildStatus !== 'ready'}
            />

            {/* Suggestions */}
            {suggestions.length > 0 && cards.length === 0 && (
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s, i) => (
                  <button key={i} onClick={() => handleQuery(s)}
                    className="text-xs px-3 py-1.5 bg-white border border-surface-200 rounded-full text-surface-600 hover:border-axl-400 hover:text-axl-700 transition-colors">
                    {s}
                  </button>
                ))}
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="p-4 bg-red-50 border border-red-100 rounded-xl text-red-700 text-sm">
                {error}
              </div>
            )}

            {/* Loading */}
            {loading && (
              <div className="flex items-center gap-3 text-surface-400 text-sm">
                <div className="w-5 h-5 border-2 border-axl-400 border-t-transparent rounded-full animate-spin" />
                Computing insight...
              </div>
            )}

            {/* Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {cards.map(card => (
                <InsightCard 
                  key={card.id} 
                  card={card} 
                  onAddToDashboard={addToDashboard}
                  onAlert={setAlertCard}
                  compact={false}
                />
              ))}
            </div>

            {/* Empty State */}
            {buildStatus === 'idle' && cards.length === 0 && (
              <div className="text-center py-20">
                <div className="w-16 h-16 mx-auto rounded-2xl bg-axl-50 flex items-center justify-center mb-4">
                  <ChartIcon />
                </div>
                <h3 className="text-lg font-semibold text-surface-900">Upload data to begin</h3>
                <p className="text-sm text-surface-400 mt-1 max-w-sm mx-auto">
                  Upload a CSV, Excel, or Parquet file. We'll auto-detect dimensions, measures, and time — then build a pre-computed cube for instant queries.
                </p>
              </div>
            )}
          </div>
        ) : (
          <DashboardBuilder 
            cards={dashboardCards} 
            onRemove={i => setDashboardCards(prev => prev.filter((_, idx) => idx !== i))}
            onSave={() => {}}
            onLayoutChange={setLayout}
            layout={layout}
          />
        )}
      </main>

      {/* Alert Modal */}
      {alertCard && (
        <AlertModal 
          card={alertCard} 
          onClose={() => setAlertCard(null)} 
          onCreate={() => setAlertCard(null)}
          sessionId={sessionId}
        />
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
