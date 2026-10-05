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

function Sources() {
  const [sources, setSources] = useState<Source[]>([]);
  const [name, setName] = useState('Telegram Signals');
  const [identifier, setIdentifier] = useState('');
  const [error, setError] = useState('');

  const refresh = () => apiGet<Source[]>('/api/v1/sources').then(setSources).catch(e => setError(String(e)));
  useEffect(() => {
  void refresh();
}, []);

  async function addSource(e: React.FormEvent) {
    e.preventDefault(); setError('');
    try {
      await apiPost('/api/v1/sources', { name, type: 'telegram', identifier, enabled: true, config: {} });
      setIdentifier(''); refresh();
    } catch (e) { setError(String(e)); }
  }

  return <>
    <header><div><h1>Sources</h1><p>Connect data origins without coupling them to any strategy.</p></div></header>
    <section className="panel">
      <h2>Add Telegram source</h2>
      <form onSubmit={addSource} className="formrow">
        <input value={name} onChange={e => setName(e.target.value)} placeholder="Name" />
        <input value={identifier} onChange={e => setIdentifier(e.target.value)} placeholder="@channel or channel identifier" required />
        <button className="primary" type="submit">Add source</button>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
    <section className="panel">
      <h2>Configured sources</h2>
      {sources.length === 0 ? <p>No sources yet.</p> : <div className="table">
        {sources.map(source => <div className="row" key={source.id}>
          <span>{source.name}</span><code>{source.identifier}</code><span>{source.type}</span><span>{source.status}</span>
        </div>)}
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
      <div className="panel"><h2>Raw message</h2><textarea value={text} onChange={e => setText(e.target.value)} /><button className="primary" onClick={parse}>Parse signal</button>{error && <p className="error">{error}</p>}</div>
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
