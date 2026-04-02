import { useEffect, useState } from 'react';
import { Play, Loader2, Sparkles, ChevronDown, ChevronUp, Save } from 'lucide-react';
import { forgeDiscoveryApi } from '../services/api';
import { ArtistResultCard, DiscoveryPipelineViz } from '../components/forge';
import { Toggle } from '../components/common';
import type { ForgeDiscoveryConfig, ForgeDiscoveryResult } from '../types';

const SEED_PERIODS: Array<{ value: ForgeDiscoveryConfig['seed_period']; label: string }> = [
  { value: '7day', label: 'Last 7 days' },
  { value: '1month', label: 'Last 30 days' },
  { value: '3month', label: 'Last 3 months' },
  { value: '6month', label: 'Last 6 months' },
  { value: '12month', label: 'Last 12 months' },
  { value: 'overall', label: 'All time' },
];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 || 12;
  const ampm = i < 12 ? 'AM' : 'PM';
  return { value: i, label: `${h}:00 ${ampm}` };
});

interface PDConfig extends ForgeDiscoveryConfig {
  run_mode: 'build' | 'fetch';
  auto_publish: boolean;
  schedule_enabled: boolean;
  schedule_weekday: number;
  schedule_hour: number;
  dry_run: boolean;
}

interface ForgeCustomDiscoveryProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function ForgeCustomDiscovery({ toast }: ForgeCustomDiscoveryProps) {
  const [config, setConfig] = useState<PDConfig>({
    closeness: 5,
    seed_period: '1month',
    min_scrobbles: 10,
    max_tracks: 50,
    run_mode: 'build',
    auto_publish: false,
    schedule_enabled: false,
    schedule_weekday: 1,
    schedule_hour: 8,
    dry_run: false,
  });
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ForgeDiscoveryResult[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const server = await forgeDiscoveryApi.getConfig();
        if (cancelled) return;
        setConfig(prev => ({
          ...prev,
          ...server,
          run_mode: server.run_mode === 'fetch' ? 'fetch' : 'build',
        }));
      } catch {
        if (!cancelled) {
          toast.error('Failed to load Custom Discovery config');
        }
      } finally {
        if (!cancelled) {
          setLoadingConfig(false);
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [toast]);

  const update = <K extends keyof PDConfig>(key: K, value: PDConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await forgeDiscoveryApi.saveConfig(config);
      toast.success('Custom Discovery config saved');
    } catch {
      toast.error('Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    setRunning(true);
    try {
      const data = await forgeDiscoveryApi.run({ ...config, run_mode: 'build' });
      setResults(data.artists);
      toast.success('Custom Discovery complete');
    } catch {
      toast.error('Failed to run Custom Discovery');
    } finally {
      setRunning(false);
    }
  };

  const closenessLabel = config.closeness <= 3
    ? 'Very close to my taste'
    : config.closeness <= 5
    ? 'Balanced'
    : 'More adventurous';

  return (
    <div className="space-y-8">
      {/* Workspace: config form */}
      <div>
        <div className="pb-3 border-b border-[#1a1a1a] mb-6">
          <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Configuration</span>
        </div>

        <div className="space-y-7">
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="label mb-0">Closeness to Taste</label>
              <span className="text-accent text-xs font-semibold">{closenessLabel}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[#444] text-xs whitespace-nowrap">Closer</span>
              <div className="flex-1 relative">
                <input
                  type="range" min={1} max={9} step={1}
                  value={config.closeness}
                  onChange={e => update('closeness', Number(e.target.value))}
                  className="w-full accent-accent h-1.5"
                />
                <div className="flex justify-between text-[#2a2a2a] text-[10px] mt-0.5">
                  {Array.from({ length: 9 }, (_, i) => (
                    <span key={i} className={`w-px text-center ${config.closeness === i + 1 ? 'text-accent' : ''}`}>|</span>
                  ))}
                </div>
              </div>
              <span className="text-[#444] text-xs whitespace-nowrap">More varied</span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="label">Scrobbling Period</label>
              <select className="select" value={config.seed_period} onChange={e => update('seed_period', e.target.value as ForgeDiscoveryConfig['seed_period'])}>
                {SEED_PERIODS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Min Artist Scrobbles</label>
              <input type="number" className="input" min={1} value={config.min_scrobbles} onChange={e => update('min_scrobbles', Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Max Build Size</label>
              <input type="number" className="input" min={5} max={200} value={config.max_tracks} onChange={e => update('max_tracks', Number(e.target.value))} />
            </div>
          </div>
        </div>
      </div>

      <DiscoveryPipelineViz runMode="build" />

      <section className="border-t border-[#1a1a1a] pt-6">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-5">Automation</h2>
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <Toggle on={config.auto_publish} onChange={v => update('auto_publish', v)} />
            <div>
              <p className="text-text-primary text-sm font-medium">Auto Publish</p>
              <p className="text-[#444] text-xs">Publish automatically after build approval (Phase 27d)</p>
            </div>
          </div>
          <div className="w-px h-8 bg-[#1a1a1a] self-center" />
          <div className="flex items-center gap-3">
            <Toggle on={config.schedule_enabled} onChange={v => update('schedule_enabled', v)} />
            <div>
              <p className="text-text-primary text-sm font-medium">Weekly Schedule</p>
              <p className="text-[#444] text-xs">Run on a set day and time</p>
            </div>
            <select className="select !w-auto ml-2" value={config.schedule_weekday} onChange={e => update('schedule_weekday', Number(e.target.value))}>
              {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select>
            <select className="select !w-auto" value={config.schedule_hour} onChange={e => update('schedule_hour', Number(e.target.value))}>
              {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </div>
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-6">
        <button onClick={() => setAdvanced(a => !a)} className="flex items-center justify-between w-full text-left">
          <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">Advanced</span>
          {advanced ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
        </button>
        {advanced && (
          <div className="mt-5 space-y-5">
            <div>
              <label className="label">Ignore Keywords</label>
              <input className="input" placeholder="christmas, tribute, karaoke" />
              <p className="text-[#444] text-xs mt-1">Comma-separated - artists matching any keyword will be skipped</p>
            </div>
            <div>
              <label className="label">Ignore Artists</label>
              <input className="input" placeholder="Artist One, Artist Two" />
              <p className="text-[#444] text-xs mt-1">Comma-separated - these artists will never surface in results</p>
            </div>
            <div>
              <label className="label">Custom Build Name</label>
              <input className="input" placeholder="My Discovery" />
              <p className="text-[#444] text-xs mt-1">Override the default build name shown in Builder</p>
            </div>
            <div className="border-t border-[#1a1a1a] pt-5 flex items-center gap-3">
              <Toggle on={config.dry_run} onChange={v => update('dry_run', v)} />
              <div>
                <p className="text-text-primary text-sm font-medium">Dry Run</p>
                <p className="text-[#444] text-xs mt-0.5">Simulate a run without making any changes</p>
              </div>
            </div>
          </div>
        )}
      </section>

      <div className="flex gap-3 pt-2 border-t border-[#1a1a1a]">
        <button onClick={handleRun} disabled={running || saving || loadingConfig} className="btn-primary flex items-center gap-2 text-sm">
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {running ? 'Discovering...' : 'Run Discovery'}
        </button>
        <button onClick={handleSave} disabled={saving || running || loadingConfig} className="btn-secondary flex items-center gap-2 text-sm">
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save Config
        </button>
      </div>

      {/* History ledger: discovery results */}
      {running && (
        <div className="space-y-3 pt-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-[72px] bg-[#0d0d0d] border border-[#1a1a1a] animate-pulse" />
          ))}
        </div>
      )}

      {!running && results !== null && (
        <section className="border-t border-[#1a1a1a] pt-8">
          <div className="flex items-center gap-2 mb-4">
            <Sparkles size={14} className="text-accent" />
            <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">
              {results.length} artist{results.length !== 1 ? 's' : ''} discovered
            </span>
          </div>
          {results.length === 0 ? (
            <div className="py-12 text-center space-y-2">
              <p className="text-text-muted text-sm">No new artists found for these settings</p>
              <p className="text-[#444] text-xs">Try increasing the closeness value or expanding the seed period</p>
            </div>
          ) : (
            <div className="space-y-2">
              {results.map((r, i) => <ArtistResultCard key={i} result={r} />)}
            </div>
          )}
        </section>
      )}

      {!running && results === null && (
        <div className="py-12 text-center border border-dashed border-[#1e1e1e] space-y-2">
          <Sparkles size={22} className="text-[#2a2a2a] mx-auto" />
          <p className="text-[#444] text-sm">Configure and run to discover new artists</p>
          {/* GAP-03: backend stub - results will be empty until Custom Discovery engine is implemented */}
        </div>
      )}
    </div>
  );
}

