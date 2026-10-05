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
  config: Record<string, unknown>;
};

type RawMessage = {
  id: number;
  external_id: string;
  occurred_at: string;
  ingested_at: string;
  event_type: string;
  text: string | null;
  edit_date: string | null;
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

type ActionState = {
  loading?: string;
  message?: string;
  error?: string;
};

const sections = ['Dashboard', 'Sources', 'Signal Lab', 'Backtests', 'Optimizer', 'Strategies', 'Paper Trading', 'Live Trading', 'Portfolio', 'Exchanges', 'Market Data', 'Logs', 'Settings'];

function Dashboard({ capabilities }: { capabilities: Capability | null }) {
  return <>
    <header>
      <div><h1>Research Dashboard</h1><p>One pipeline from raw data to validated execution.</p></div>
      <span className="pill danger">LIVE {capabilities?.live_trading_enabled ? 'ENABLED' : 'DISABLED'}</span>
    </header>
    <section className="grid">
      <article><span>Strategies</span><strong>Plugin-based</strong><small>Telegram, news, technical, ML, custom</small></article>
      <article><span>Data Sources</span><strong>{capabilities?.data_sources.length ?? '—'}</strong><small>Interchangeable source adapters</small></article>
      <article><span>Markets</span><strong>Spot V1</strong><small>Futures / MT5 / multi-asset ready</small></article>
      <article><span>Safety Stage</span><strong>DRAFT</strong><small>Backtest → Validate → Paper → Approve → Live</small></article>
    </section>
    <section className="panel flow">
      <h2>Talos processing pipeline</h2>
      <div className="flowline">Sources → Ingestion → Normalization → Strategy → Standard Signal → Backtest → Optimization → Paper → Risk → Router → Execution</div>
      <p>The same downstream research and execution stack is reused no matter where a strategy gets its data.</p>
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

function Sources() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState('Telegram Signals');
  const [identifier, setIdentifier] = useState('');
  const [error, setError] = useState('');
  const [action, setAction] = useState<Record<number, ActionState>>({});
  const [messages, setMessages] = useState<Record<number, RawMessage[]>>({});
  const [signals, setSignals] = useState<Record<number, StoredSignal[]>>({});
  const [expanded, setExpanded] = useState<Record<number, 'messages' | 'signals' | undefined>>({});

  const refresh = () => apiGet<Source[]>('/api/v1/sources').then(setSources).catch(e => setError(String(e)));
  useEffect(() => { void refresh(); }, []);

  function setActionState(sourceId: number, state: ActionState) {
    setAction(prev => ({ ...prev, [sourceId]: state }));
  }

  async function addSource(e: React.FormEvent) {
    e.preventDefault(); setError('');
    try {
      await apiPost('/api/v1/sources', { name, type: 'telegram', identifier, enabled: true, config: {} });
      setIdentifier('');
      await refresh();
    } catch (e) { setError(String(e)); }
  }

  async function testAccess(source: Source) {
    setActionState(source.id, { loading: 'Testing access…' });
    try {
      const result = await apiPost<{ title?: string; kind?: string; can_read_history: boolean }>(`/api/v1/sources/${source.id}/test-access`, {});
      setActionState(source.id, { message: `Connected${result.title ? ` to ${result.title}` : ''}. History access: ${result.can_read_history ? 'yes' : 'no'}.` });
      await refresh();
    } catch (e) { setActionState(source.id, { error: String(e) }); await refresh(); }
  }

  async function importHistory(source: Source) {
    setActionState(source.id, { loading: 'Importing up to 1,000 historical messages…' });
    try {
      const result = await apiPost<{ fetched: number; inserted: number; skipped: number; history_complete?: boolean }>(`/api/v1/sources/${source.id}/import-history?limit=1000`, {});
      const suffix = result.history_complete ? ' History cursor reached the current end.' : ' Click Import History again for the next batch.';
      setActionState(source.id, { message: `Fetched ${result.fetched}; saved ${result.inserted}; duplicates ${result.skipped}.${suffix}` });
      await refresh();
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function syncLatest(source: Source) {
    setActionState(source.id, { loading: 'Syncing latest messages…' });
    try {
      const result = await apiPost<{ fetched: number; inserted: number; skipped: number }>(`/api/v1/sources/${source.id}/sync?limit=250`, {});
      setActionState(source.id, { message: `Latest sync: fetched ${result.fetched}; saved ${result.inserted}; duplicates ${result.skipped}.` });
      await refresh();
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function viewMessages(source: Source) {
    setActionState(source.id, { loading: 'Loading messages…' });
    try {
      const rows = await apiGet<RawMessage[]>(`/api/v1/sources/${source.id}/messages?limit=100`);
      setMessages(prev => ({ ...prev, [source.id]: rows }));
      setExpanded(prev => ({ ...prev, [source.id]: 'messages' }));
      setActionState(source.id, { message: `Showing ${rows.length} most recent stored messages.` });
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  async function parseSignals(source: Source) {
    setActionState(source.id, { loading: 'Parsing stored messages…' });
    try {
      const result = await apiPost<{ examined: number; parsed: number; needs_review: number; ignored: number; created: number; updated: number }>(`/api/v1/sources/${source.id}/parse-signals?limit=5000`, {});
      setActionState(source.id, { message: `Examined ${result.examined}: parsed ${result.parsed}, review ${result.needs_review}, ignored ${result.ignored}. New ${result.created}, updated ${result.updated}.` });
      const rows = await apiGet<StoredSignal[]>(`/api/v1/sources/${source.id}/signals?limit=100`);
      setSignals(prev => ({ ...prev, [source.id]: rows }));
      setExpanded(prev => ({ ...prev, [source.id]: 'signals' }));
    } catch (e) { setActionState(source.id, { error: String(e) }); }
  }

  return <>
    <header><div><h1>Sources</h1><p>Connect data origins without coupling them to any strategy.</p></div></header>
    <section className="panel">
      <h2>Add Telegram source</h2>
      <form onSubmit={addSource} className="formrow">
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Name" />
        <input value={identifier} onChange={e => setIdentifier(e.target.value)} placeholder="@channel, t.me link, invite link or peer ID" required />
        <button className="primary" type="submit">Add source</button>
      </form>
      <p className="hint">For private channels, the dedicated Talos Telegram account must already be a member.</p>
      {error && <p className="error">{error}</p>}
    </section>

    <section className="panel">
      <h2>Configured sources</h2>
      {sources.length === 0 ? <p>No sources yet.</p> : <div className="source-list">
        {sources.map(source => {
          const connection = sourceConnectionLabel(source);
          const state = action[source.id] ?? {};
          const historyComplete = source.config?.history_complete === true;
          return <div className="source-card" key={source.id}>
            <div className="source-head">
              <div><strong>{source.name}</strong><code>{source.identifier}</code></div>
              <span className={`pill ${connection.cls}`}>{connection.text}</span>
            </div>
            <div className="source-meta">
              <span>Type: {source.type}</span>
              <span>History: {historyComplete ? 'complete' : (source.config?.history_cursor_id ? 'partial' : 'not imported')}</span>
              {typeof source.config?.telegram_title === 'string' && <span>Chat: {source.config.telegram_title}</span>}
            </div>
            <div className="actions">
              <button onClick={() => void testAccess(source)}>Test Access</button>
              <button onClick={() => void importHistory(source)}>Import History</button>
              <button onClick={() => void syncLatest(source)}>Sync New Messages</button>
              <button onClick={() => void viewMessages(source)}>View Messages</button>
              <button className="accent" onClick={() => void parseSignals(source)}>Parse Signals</button>
            </div>
            {state.loading && <p className="statusline">{state.loading}</p>}
            {state.message && <p className="success">{state.message}</p>}
            {state.error && <p className="error">{state.error}</p>}

            {expanded[source.id] === 'messages' && <div className="data-view">
              <div className="data-view-title"><strong>Stored messages</strong><button onClick={() => setExpanded(prev => ({ ...prev, [source.id]: undefined }))}>Close</button></div>
              {(messages[source.id] ?? []).length === 0 ? <p>No stored messages yet.</p> : (messages[source.id] ?? []).map(row => <div className="message-item" key={row.id}>
                <div className="message-meta"><span>#{row.external_id}</span><span>{new Date(row.occurred_at).toLocaleString()}</span>{row.edit_date && <span className="edited">edited</span>}</div>
                <pre className="message-text">{row.text || '(no text)'}</pre>
              </div>)}
            </div>}

            {expanded[source.id] === 'signals' && <div className="data-view">
              <div className="data-view-title"><strong>Parsed signals</strong><button onClick={() => setExpanded(prev => ({ ...prev, [source.id]: undefined }))}>Close</button></div>
              {(signals[source.id] ?? []).length === 0 ? <p>No signals detected yet.</p> : <div className="signal-table">
                {(signals[source.id] ?? []).map(row => <div className="signal-row" key={row.id}>
                  <strong>{row.symbol}</strong><span>{row.side.toUpperCase()}</span><span>{Math.round(row.confidence * 100)}%</span><span className={row.status === 'parsed' ? 'success-inline' : 'warning-inline'}>{row.status}</span><span>{new Date(row.timestamp).toLocaleString()}</span>
                </div>)}
              </div>}
            </div>}
          </div>;
        })}
      </div>}
    </section>
  </>;
}

const sample = `🟩 RLC|USDT\n\n⚜ BUY : 0.41 - 0.43\n\n🎯:\n1 : 0.440\n2 : 0.450\n3 : 0.470\n4 : 0.500\n5 : 0.540\n\n🔴 STOP اغلاق 4 ساعات اقل من 0.40`;

function SignalLab() {
  const [text, setText] = useState(sample);
  const [result, setResult] = useState<ParseResult | null>(null);
  const [error, setError] = useState('');

  async function parse() {
    setError('');
    try {
      const value = await apiPost<ParseResult>('/api/v1/signals/parse', { text, default_quote: 'USDT' });
      setResult(value);
    } catch (e) { setError(String(e)); }
  }

  return <>
    <header><div><h1>Signal Lab</h1><p>Test raw source text against Talos normalization before it enters research.</p></div></header>
    <section className="split">
      <div className="panel"><h2>Raw message</h2><textarea value={text} onChange={e => setText(e.target.value)} /><button className="primary" onClick={() => void parse()}>Parse signal</button>{error && <p className="error">{error}</p>}</div>
      <div className="panel"><h2>Normalized output</h2>{result ? <pre>{JSON.stringify(result, null, 2)}</pre> : <p>Run the parser to inspect the normalized schema.</p>}</div>
    </section>
  </>;
}

function Placeholder({ name }: { name: string }) {
  return <><header><div><h1>{name}</h1><p>This module is isolated behind a stable Talos contract and will be implemented without changing the rest of the pipeline.</p></div></header><section className="panel"><h2>Module reserved</h2><p>The architecture is ready; implementation follows the V1 milestone order.</p></section></>;
}

function App() {
  const [active, setActive] = useState('Dashboard');
  const [capabilities, setCapabilities] = useState<Capability | null>(null);
  useEffect(() => { apiGet<Capability>('/api/v1/system/capabilities').then(setCapabilities).catch(() => undefined); }, []);

  let content: React.ReactNode;
  if (active === 'Dashboard') content = <Dashboard capabilities={capabilities} />;
  else if (active === 'Sources') content = <Sources />;
  else if (active === 'Signal Lab') content = <SignalLab />;
  else content = <Placeholder name={active} />;

  return <div className="shell">
    <aside>
      <div className="brand">TALOS</div>
      <div className="tag">Research → Validate → Execute</div>
      <nav>{sections.map(s => <button className={active === s ? 'active' : ''} onClick={() => setActive(s)} key={s}>{s}</button>)}</nav>
    </aside>
    <main>{content}</main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
