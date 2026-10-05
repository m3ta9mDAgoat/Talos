import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { apiGet, apiPost } from './api';
import './styles.css';

type Capability = {
  data_sources: string[];
  markets: string[];
  future_markets: string[];
  research_stages: string[];
  live_trading_enabled: boolean;
};

type Source = {
  id: number;
  name: string;
  type: string;
  identifier: string;
  enabled: boolean;
  status: string;
  config: Record<string, any>;
};

type RawMessage = {
  id: number;
  external_id: string;
  occurred_at: string;
  ingested_at: string;
  event_type: string;
  text: string | null;
  edit_date: string | null;
  metadata?: Record<string, any>;
};

type StoredSignal = {
  id: number;
  source_message_id: string | null;
  timestamp: string;
  symbol: string;
  side: string;
  confidence: number;
  status: string;
  entry: unknown;
  stop: unknown;
  targets: number[];
  raw_text: string | null;
  metadata?: Record<string, any>;
};

type ParseResult = {
  signal: null | {
    symbol: string;
    side: string;
    entry: unknown;
    targets: number[];
    stop: unknown;
    confidence: number;
    status: string;
  };
  confidence: number;
  warnings: string[];
};

type FullImport = {
  source_id: number;
  status: string;
  running: boolean;
  fetched_total: number;
  inserted_total: number;
  skipped_total: number;
  cursor_id: number;
  history_complete: boolean;
  batches: number;
  last_error?: string | null;
};

type AnalysisRun = {
  id: number;
  source_id: number;
  status: string;
  stage: string;
  progress: number;
  config: Record<string, any>;
  summary: Record<string, any>;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
};

type AnalysisTrade = {
  id: number;
  signal_id: number;
  timestamp: string;
  symbol: string;
  exchange?: string | null;
  status: string;
  return_pct?: number | null;
  result: Record<string, any>;
};

type ReviewMessage = {
  raw_event_id: number;
  external_id: string;
  occurred_at: string;
  text: string | null;
  predicted_class: string;
  confidence: number;
  manual_class?: string | null;
  reasons: string[];
};

type ActionState = { loading?: string; message?: string; error?: string };

const sections = ['Dashboard', 'Sources', 'Signal Lab', 'Backtests', 'Optimizer', 'Strategies', 'Paper Trading', 'Live Trading', 'Portfolio', 'Exchanges', 'Market Data', 'Logs', 'Settings'];

function fmt(value: unknown, suffix = '') {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
  return String(value);
}

function Metric({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) {
  return <article><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</article>;
}

function Dashboard({ capabilities }: { capabilities: Capability | null }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [runs, setRuns] = useState<Record<number, AnalysisRun | null>>({});
  useEffect(() => {
    apiGet<Source[]>('/api/v1/sources').then(async rows => {
      setSources(rows);
      const pairs = await Promise.all(rows.map(async s => [s.id, await apiGet<AnalysisRun | null>(`/api/v1/analysis/sources/${s.id}/latest`).catch(() => null)] as const));
      setRuns(Object.fromEntries(pairs));
    }).catch(() => undefined);
  }, []);

  const completed = Object.values(runs).filter(r => r?.status === 'complete') as AnalysisRun[];
  const latest = completed.sort((a, b) => b.id - a.id)[0];
  const summary = latest?.summary ?? {};
  return <>
    <header>
      <div><h1>Research Dashboard</h1><p>Research, validate and compare strategies before any capital is exposed.</p></div>
      <span className="pill danger">LIVE {capabilities?.live_trading_enabled ? 'ENABLED' : 'DISABLED'}</span>
    </header>
    <section className="grid">
      <Metric label="Sources" value={sources.length} note="Telegram now; news / RSS / custom ready" />
      <Metric label="Latest tested trades" value={fmt(summary.trades)} note={latest ? `Analysis #${latest.id}` : 'Run a channel analysis'} />
      <Metric label="Avg trade return" value={summary.average_return_pct == null ? '—' : `${fmt(summary.average_return_pct)}%`} />
      <Metric label="Profit factor" value={fmt(summary.profit_factor)} note="After configured fees/slippage" />
    </section>
    <section className="panel flow">
      <h2>Talos pipeline</h2>
      <div className="flowline">Sources → Archive → Classify → Normalize → Strategy → Market Resolver → Backtest → Optimization → Paper → Risk → Router → Execution</div>
      <p>Telegram is one data source. New news, technical, ML or custom strategy plugins reuse the same research and execution pipeline.</p>
    </section>
  </>;
}

function sourceConnectionLabel(source: Source): { text: string; cls: string } {
  const checked = source.config?.access_checked_at;
  const verified = source.config?.access_verified;
  if (!checked) return { text: 'NOT TESTED', cls: 'muted' };
  if (verified === true) return { text: 'CONNECTED', cls: 'ok' };
  return { text: 'ACCESS ERROR', cls: 'danger' };
}

function AnalysisSummary({ run, trades }: { run: AnalysisRun; trades: AnalysisTrade[] }) {
  const s = run.summary ?? {};
  const targets = s.target_hit_rates_pct ?? {};
  return <div className="analysis-box">
    <div className="analysis-title"><div><h3>Profitability analysis</h3><span className={`pill ${run.status === 'complete' ? 'ok' : run.status === 'error' ? 'danger' : 'muted'}`}>{run.status.toUpperCase()}</span></div><span>{Math.round((run.progress || 0) * 100)}% · {run.stage}</span></div>
    {run.status === 'running' && <div className="progress"><div style={{ width: `${Math.max(2, run.progress * 100)}%` }} /></div>}
    {run.error && <p className="error">{run.error}</p>}
    {run.status === 'complete' && <>
      <div className="mini-grid">
        <Metric label="Signals tested" value={fmt(s.signals_eligible)} />
        <Metric label="Trades" value={fmt(s.trades)} />
        <Metric label="Positive trades" value={s.positive_trade_rate_pct == null ? '—' : `${fmt(s.positive_trade_rate_pct)}%`} />
        <Metric label="Average return" value={s.average_return_pct == null ? '—' : `${fmt(s.average_return_pct)}%`} />
        <Metric label="Profit factor" value={fmt(s.profit_factor)} />
        <Metric label="Model equity" value={s.model_equity_return_pct == null ? '—' : `${fmt(s.model_equity_return_pct)}%`} />
        <Metric label="Model max DD" value={s.model_max_drawdown_pct == null ? '—' : `${fmt(s.model_max_drawdown_pct)}%`} />
        <Metric label="Missing market data" value={fmt(s.market_data_missing)} />
      </div>
      <div className="two-col">
        <div className="subpanel"><h4>Target hit rates</h4>{Object.keys(targets).length === 0 ? <p>No target data.</p> : Object.entries(targets).map(([key, val]) => <div className="barrow" key={key}><span>{key.toUpperCase()}</span><div className="bar"><i style={{ width: `${Math.min(100, Number(val))}%` }} /></div><b>{fmt(val)}%</b></div>)}</div>
        <div className="subpanel"><h4>Assumptions</h4><div className="kv"><span>Timeframe</span><b>{s.assumptions?.timeframe ?? '—'}</b><span>Horizon</span><b>{s.assumptions?.horizon_days ?? '—'} days</b><span>Fees</span><b>{s.assumptions?.fee_bps ?? '—'} bps</b><span>Slippage</span><b>{s.assumptions?.slippage_bps ?? '—'} bps</b><span>Edited signals</span><b>{s.assumptions?.exclude_edited ? 'excluded' : 'included'}</b></div></div>
      </div>
      {trades.length > 0 && <div className="data-view"><h4>Recent tested signals</h4><div className="trade-table">{trades.slice(0, 100).map(t => <div className="trade-row" key={t.id}><strong>{t.symbol}</strong><span>{t.exchange ?? '—'}</span><span>{t.status}</span><span className={(t.return_pct ?? 0) >= 0 ? 'success-inline' : 'error-inline'}>{t.return_pct == null ? '—' : `${fmt(t.return_pct)}%`}</span><span>{new Date(t.timestamp).toLocaleDateString()}</span></div>)}</div></div>}
    </>}
  </div>;
}

function Sources() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState('Telegram Signals');
  const [identifier, setIdentifier] = useState('');
  const [error, setError] = useState('');
  const [action, setAction] = useState<Record<number, ActionState>>({});
  const [messages, setMessages] = useState<Record<number, RawMessage[]>>({});
  const [signals, setSignals] = useState<Record<number, StoredSignal[]>>({});
  const [expanded, setExpanded] = useState<Record<number, 'messages' | 'signals' | 'review' | undefined>>({});
  const [imports, setImports] = useState<Record<number, FullImport>>({});
  const [runs, setRuns] = useState<Record<number, AnalysisRun | null>>({});
  const [trades, setTrades] = useState<Record<number, AnalysisTrade[]>>({});
  const [reviews, setReviews] = useState<Record<number, ReviewMessage[]>>({});
  const [settings, setSettings] = useState({ timeframe: '15m', horizon_days: 14, fee_bps: 10, slippage_bps: 5, portfolio_fraction: 0.10, require_stop: true, exclude_edited: true, conservative_intrabar: true, max_signals: 500 });

  const refresh = () => apiGet<Source[]>('/api/v1/sources').then(setSources).catch(e => setError(String(e)));
  useEffect(() => { void refresh(); }, []);

  function setActionState(sourceId: number, state: ActionState) { setAction(prev => ({ ...prev, [sourceId]: state })); }

  async function addSource(e: React.FormEvent) {
    e.preventDefault(); setError('');
    try { await apiPost('/api/v1/sources', { name, type: 'telegram', identifier, enabled: true, config: {} }); setIdentifier(''); await refresh(); }
    catch (e) { setError(String(e)); }
  }

  async function testAccess(source: Source) {
    setActionState(source.id, { loading: 'Testing access…' });
    try {
      const result = await apiPost<{ title?: string; can_read_history: boolean }>(`/api/v1/sources/${source.id}/test-access`, {});
      setActionState(source.id, { message: `Connected${result.title ? ` to ${result.title}` : ''}. History: ${result.can_read_history ? 'yes' : 'no'}.` }); await refresh();
    } catch (e) { setActionState(source.id, { error: String(e) }); await refresh(); }
  }

  async function pollImport(sourceId: number) {
    try {
      const result = await apiGet<FullImport>(`/api/v1/sources/${sourceId}/import-full/status`);
      setImports(prev => ({ ...prev, [sourceId]: result }));
      if (result.running) window.setTimeout(() => void pollImport(sourceId), 1800);
      else if (result.history_complete) setActionState(sourceId, { message: `Full history complete: ${result.inserted_total.toLocaleString()} stored messages.` });
      else if (result.last_error) setActionState(sourceId, { error: result.last_error });
    } catch (e) { setActionState(sourceId, { error: String(e) }); }
  }

  async function importFull(source: Source) {
    setActionState(source.id, { loading: 'Starting full-history import…' });
    try {
      const result = await apiPost<FullImport>(`/api/v1/sources/${source.id}/import-full`, {});
      setImports(prev => ({ ...prev, [source.id]: result }));
      setActionState(source.id, { message: 'Import runs in batches automatically. You can leave this page open.' });
      window.setTimeout(() => void pollImport(source.id), 600);
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function syncLatest(source: Source) {
    setActionState(source.id, { loading: 'Syncing latest messages…' });
    try { const r = await apiPost<any>(`/api/v1/sources/${source.id}/sync?limit=250`, {}); setActionState(source.id, { message: `Fetched ${r.fetched}; saved ${r.inserted}; duplicates ${r.skipped}.` }); }
    catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function viewMessages(source: Source) {
    try { const rows = await apiGet<RawMessage[]>(`/api/v1/sources/${source.id}/messages?limit=100`); setMessages(p => ({ ...p, [source.id]: rows })); setExpanded(p => ({ ...p, [source.id]: 'messages' })); }
    catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function parseSignals(source: Source) {
    setActionState(source.id, { loading: 'Classifying and parsing stored messages…' });
    try {
      const r = await apiPost<any>(`/api/v1/sources/${source.id}/parse-signals?limit=10000`, {});
      setActionState(source.id, { message: `Examined ${r.examined}: parsed ${r.parsed}, review ${r.needs_review}, ignored ${r.ignored}.` });
      const rows = await apiGet<StoredSignal[]>(`/api/v1/sources/${source.id}/signals?limit=100`); setSignals(p => ({ ...p, [source.id]: rows })); setExpanded(p => ({ ...p, [source.id]: 'signals' }));
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function pollAnalysis(sourceId: number, runId: number) {
    try {
      const run = await apiGet<AnalysisRun>(`/api/v1/analysis/runs/${runId}`); setRuns(p => ({ ...p, [sourceId]: run }));
      if (run.status === 'running' || run.status === 'queued') window.setTimeout(() => void pollAnalysis(sourceId, runId), 2200);
      else if (run.status === 'complete') {
        const rows = await apiGet<AnalysisTrade[]>(`/api/v1/analysis/runs/${runId}/trades?limit=300`); setTrades(p => ({ ...p, [sourceId]: rows }));
        setActionState(sourceId, { message: 'Analysis complete.' });
      } else if (run.error) setActionState(sourceId, { error: run.error });
    } catch (e) { setActionState(sourceId, { error: String(e) }); }
  }

  async function analyze(source: Source) {
    setActionState(source.id, { loading: 'Starting profitability analysis…' });
    try {
      const run = await apiPost<AnalysisRun>(`/api/v1/analysis/sources/${source.id}/run`, settings); setRuns(p => ({ ...p, [source.id]: run }));
      window.setTimeout(() => void pollAnalysis(source.id, run.id), 700);
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function loadLatestAnalysis(source: Source) {
    try {
      const run = await apiGet<AnalysisRun | null>(`/api/v1/analysis/sources/${source.id}/latest`); setRuns(p => ({ ...p, [source.id]: run }));
      if (run) {
        if (run.status === 'running') void pollAnalysis(source.id, run.id);
        if (run.status === 'complete') { const rows = await apiGet<AnalysisTrade[]>(`/api/v1/analysis/runs/${run.id}/trades?limit=300`); setTrades(p => ({ ...p, [source.id]: rows })); }
      }
    } catch { /* optional */ }
  }

  async function reviewQueue(source: Source) {
    try { const rows = await apiGet<ReviewMessage[]>(`/api/v1/analysis/sources/${source.id}/review?limit=100`); setReviews(p => ({ ...p, [source.id]: rows })); setExpanded(p => ({ ...p, [source.id]: 'review' })); }
    catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function label(sourceId: number, rawEventId: number, messageClass: string) {
    await apiPost(`/api/v1/analysis/messages/${rawEventId}/label`, { message_class: messageClass });
    setReviews(prev => ({ ...prev, [sourceId]: (prev[sourceId] ?? []).map(x => x.raw_event_id === rawEventId ? { ...x, manual_class: messageClass } : x) }));
  }

  return <>
    <header><div><h1>Sources & Channel Research</h1><p>Archive a channel once, classify its messages, then test the recommendations against historical market data.</p></div></header>
    <section className="panel">
      <h2>Add Telegram source</h2>
      <form onSubmit={addSource} className="formrow"><input value={name} onChange={e => setName(e.target.value)} placeholder="Name" /><input value={identifier} onChange={e => setIdentifier(e.target.value)} placeholder="@channel, t.me link, invite link or peer ID" required /><button className="primary" type="submit">Add source</button></form>
      <p className="hint">Private channel: the dedicated Talos Telegram account must already be a member.</p>{error && <p className="error">{error}</p>}
    </section>

    <section className="panel settings-strip"><h2>Analysis defaults</h2><div className="settings-grid"><label>Timeframe<select value={settings.timeframe} onChange={e => setSettings({ ...settings, timeframe: e.target.value })}><option>5m</option><option>15m</option><option>30m</option><option>1h</option></select></label><label>Horizon days<input type="number" value={settings.horizon_days} onChange={e => setSettings({ ...settings, horizon_days: Number(e.target.value) })} /></label><label>Fee bps<input type="number" value={settings.fee_bps} onChange={e => setSettings({ ...settings, fee_bps: Number(e.target.value) })} /></label><label>Slippage bps<input type="number" value={settings.slippage_bps} onChange={e => setSettings({ ...settings, slippage_bps: Number(e.target.value) })} /></label><label>Capital/trade<input type="number" step="0.01" min="0.01" max="1" value={settings.portfolio_fraction} onChange={e => setSettings({ ...settings, portfolio_fraction: Number(e.target.value) })} /></label><label className="check"><input type="checkbox" checked={settings.require_stop} onChange={e => setSettings({ ...settings, require_stop: e.target.checked })} />Require stop</label><label className="check"><input type="checkbox" checked={settings.exclude_edited} onChange={e => setSettings({ ...settings, exclude_edited: e.target.checked })} />Exclude edited</label></div></section>

    <section className="panel"><h2>Configured sources</h2>{sources.length === 0 ? <p>No sources yet.</p> : <div className="source-list">{sources.map(source => {
      const connection = sourceConnectionLabel(source); const state = action[source.id] ?? {}; const imp = imports[source.id]; const run = runs[source.id];
      return <div className="source-card" key={source.id}>
        <div className="source-head"><div><strong>{source.name}</strong><code>{source.identifier}</code></div><span className={`pill ${connection.cls}`}>{connection.text}</span></div>
        <div className="source-meta"><span>History: {imp?.history_complete || source.config?.history_complete ? 'complete' : 'partial / not imported'}</span>{imp && <span>{imp.inserted_total.toLocaleString()} messages · {imp.batches} batches</span>}</div>
        {imp?.running && <div className="progress indeterminate"><div /></div>}
        <div className="actions"><button onClick={() => void testAccess(source)}>Test Access</button><button className="accent" onClick={() => void importFull(source)}>Import Full History</button><button onClick={() => void syncLatest(source)}>Sync New</button><button onClick={() => void viewMessages(source)}>Messages</button><button onClick={() => void parseSignals(source)}>Classify + Parse</button><button onClick={() => void reviewQueue(source)}>Review uncertain</button><button className="analyze" onClick={() => void analyze(source)}>Analyze Profitability</button><button onClick={() => void loadLatestAnalysis(source)}>Load Last Analysis</button></div>
        {state.loading && <p className="statusline">{state.loading}</p>}{state.message && <p className="success">{state.message}</p>}{state.error && <p className="error">{state.error}</p>}
        {run && <AnalysisSummary run={run} trades={trades[source.id] ?? []} />}
        {expanded[source.id] === 'messages' && <div className="data-view"><div className="data-view-title"><strong>Stored messages</strong><button onClick={() => setExpanded(p => ({ ...p, [source.id]: undefined }))}>Close</button></div>{(messages[source.id] ?? []).map(row => <div className="message-item" key={row.id}><div className="message-meta"><span>#{row.external_id}</span><span>{new Date(row.occurred_at).toLocaleString()}</span>{row.edit_date && <span className="edited">edited</span>}{row.metadata?.classification?.class && <span>{row.metadata.classification.class}</span>}</div><pre className="message-text">{row.text || '(no text)'}</pre></div>)}</div>}
        {expanded[source.id] === 'signals' && <div className="data-view"><div className="data-view-title"><strong>Parsed signals</strong><button onClick={() => setExpanded(p => ({ ...p, [source.id]: undefined }))}>Close</button></div><div className="signal-table">{(signals[source.id] ?? []).map(row => <div className="signal-row" key={row.id}><strong>{row.symbol}</strong><span>{row.side.toUpperCase()}</span><span>{Math.round(row.confidence * 100)}%</span><span className={row.status === 'parsed' ? 'success-inline' : 'warning-inline'}>{row.status}</span><span>{new Date(row.timestamp).toLocaleString()}</span></div>)}</div></div>}
        {expanded[source.id] === 'review' && <div className="data-view"><div className="data-view-title"><strong>Uncertain classifications</strong><button onClick={() => setExpanded(p => ({ ...p, [source.id]: undefined }))}>Close</button></div>{(reviews[source.id] ?? []).map(row => <div className="review-item" key={row.raw_event_id}><div className="message-meta"><span>{row.predicted_class} · {Math.round(row.confidence * 100)}%</span>{row.manual_class && <span className="success-inline">labeled: {row.manual_class}</span>}</div><p>{row.text}</p><div className="label-actions">{['new_signal','signal_update','result_update','market_commentary','news','admin','chatter','unknown'].map(c => <button key={c} onClick={() => void label(source.id, row.raw_event_id, c)}>{c}</button>)}</div></div>)}</div>}
      </div>;
    })}</div>}</section>
  </>;
}

const sample = `🟩 RLC|USDT\n\n⚜ BUY : 0.41 - 0.43\n\n🎯:\n1 : 0.440\n2 : 0.450\n3 : 0.470\n4 : 0.500\n5 : 0.540\n\n🔴 STOP اغلاق 4 ساعات اقل من 0.40`;

function SignalLab() {
  const [text, setText] = useState(sample); const [result, setResult] = useState<ParseResult | null>(null); const [error, setError] = useState('');
  async function parse() { setError(''); try { setResult(await apiPost<ParseResult>('/api/v1/signals/parse', { text, default_quote: 'USDT' })); } catch (e) { setError(String(e)); } }
  return <><header><div><h1>Signal Lab</h1><p>Test raw source text against Talos normalization.</p></div></header><section className="split"><div className="panel"><h2>Raw message</h2><textarea value={text} onChange={e => setText(e.target.value)} /><button className="primary" onClick={() => void parse()}>Parse signal</button>{error && <p className="error">{error}</p>}</div><div className="panel"><h2>Normalized output</h2>{result ? <pre>{JSON.stringify(result, null, 2)}</pre> : <p>Run the parser.</p>}</div></section></>;
}

function Placeholder({ name }: { name: string }) { return <><header><div><h1>{name}</h1><p>The contract is already isolated so this module can be added without rebuilding Talos.</p></div></header><section className="panel"><h2>Foundation ready</h2><p>This module plugs into the shared source → strategy → research → risk → execution architecture.</p></section></>; }

function App() {
  const [active, setActive] = useState('Dashboard'); const [capabilities, setCapabilities] = useState<Capability | null>(null);
  useEffect(() => { apiGet<Capability>('/api/v1/system/capabilities').then(setCapabilities).catch(() => undefined); }, []);
  let content: React.ReactNode;
  if (active === 'Dashboard') content = <Dashboard capabilities={capabilities} />;
  else if (active === 'Sources' || active === 'Backtests') content = <Sources />;
  else if (active === 'Signal Lab') content = <SignalLab />;
  else content = <Placeholder name={active} />;
  return <div className="shell"><aside><div className="brand">TALOS</div><div className="tag">Research → Validate → Execute</div><nav>{sections.map(s => <button className={active === s ? 'active' : ''} onClick={() => setActive(s)} key={s}>{s}</button>)}</nav></aside><main>{content}</main></div>;
}

createRoot(document.getElementById('root')!).render(<App />);
