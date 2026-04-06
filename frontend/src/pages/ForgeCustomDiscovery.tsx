import { useEffect, useState } from 'react';
import { Play, Loader2, Sparkles, ChevronDown, ChevronUp, Save } from 'lucide-react';
import { forgeBuildsApi, forgeDiscoveryApi } from '../services/api';
import { ArtistResultCard, DiscoveryPipelineViz } from '../components/forge';
import { Toggle } from '../components/common';
import { useForgePipelineStore } from '../stores/useForgePipelineStore';
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
  exclude_owned_artists: boolean;
  avoid_repeat_tracks: boolean;
  track_repeat_cooldown_days: number;
  cache_ttl_days: number;
  fetch_wait_timeout_s: number;
  build_name_override: string;
  ignore_keywords: string;
  ignore_artists: string;
}

interface ForgeCustomDiscoveryProps {
  toast: { success: (m: string) => void; error: (m: string) => void; info: (m: string) => void };
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
    exclude_owned_artists: false,
    avoid_repeat_tracks: true,
    track_repeat_cooldown_days: 42,
    cache_ttl_days: 30,
    fetch_wait_timeout_s: 600,
    build_name_override: '',
    ignore_keywords: '',
    ignore_artists: '',
  });
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ForgeDiscoveryResult[] | null>(null);
  const pipelineState = useForgePipelineStore(s => s.pipelines.custom_discovery);
  const resetPipeline = useForgePipelineStore(s => s.resetPipeline);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [server, persistedResults] = await Promise.all([
          forgeDiscoveryApi.getConfig(),
          forgeDiscoveryApi.getResults().catch(() => [] as ForgeDiscoveryResult[]),
        ]);
        if (cancelled) return;
        setConfig(prev => ({
          ...prev,
          ...server,
          run_mode: server.run_mode === 'fetch' ? 'fetch' : 'build',
        }));
        if (persistedResults.length > 0) {
          setResults(persistedResults);
        }
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
    resetPipeline('custom_discovery');
    setRunning(true);
    toast.info('Forge in progress: Custom Discovery run started');
    try {
      const data = await forgeDiscoveryApi.run({ ...config, run_mode: 'build' });
      setResults(data.artists);

      let queued = false;
      try {
        const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
        const customName = config.build_name_override.trim();
        await forgeBuildsApi.create({
          name: customName || `Custom Discovery ${stamp}`,
          source: 'custom_discovery',
          status: 'ready',
          run_mode: 'build',
          track_list: data.artists as unknown as Array<Record<string, unknown>>,
          summary: {
            artists_found: data.artists_found,
            built_tracks: data.built_tracks ?? data.artists_found,
            target_tracks: data.target_tracks ?? config.max_tracks,
            owned_count: data.owned_count ?? 0,
            missing_count: data.missing_count ?? 0,
            seed_artists_count: data.seed_artists_count ?? 0,
            seed_period: config.seed_period,
            max_tracks: config.max_tracks,
            closeness: config.closeness,
            run_mode: config.run_mode,
            avoid_repeat_tracks: config.avoid_repeat_tracks,
            track_repeat_cooldown_days: config.track_repeat_cooldown_days,
            cache_ttl_days: config.cache_ttl_days,
            exclude_owned_artists: config.exclude_owned_artists,
          },
        });
        queued = true;
      } catch {
        toast.error('Discovery completed, but build queueing failed');
      }

      toast.success(queued ? 'Custom Discovery complete and queued in Builder' : 'Custom Discovery complete');
    } catch {
      toast.error('Failed to run Custom Discovery');
    } finally {
      setRunning(false);
    }
  };

  const handleSaveSchedule = async () => {
    setSavingSchedule(true);
    try {
      await forgeDiscoveryApi.saveConfig({
        schedule_enabled: config.schedule_enabled,
        schedule_weekday: config.schedule_weekday,
        schedule_hour: config.schedule_hour,
      });
      toast.success('Custom Discovery schedule saved');
    } catch {
      toast.error('Failed to save Custom Discovery schedule');
    } finally {
      setSavingSchedule(false);
    }
  };

  const closenessLabel = config.closeness <= 3
    ? 'Very close to my taste'
    : config.closeness <= 5
    ? 'Balanced'
    : 'More adventurous';
  const pipelinePct = pipelineState.total > 0
    ? Math.min(100, Math.max(0, Math.round((pipelineState.processed / pipelineState.total) * 100)))
    : 0;

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
            <button
              onClick={handleSaveSchedule}
              disabled={savingSchedule || running || loadingConfig}
              className="btn-secondary text-xs px-3 py-1.5 inline-flex items-center gap-2 disabled:opacity-40"
            >
              {savingSchedule ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              Set Schedule
            </button>
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
              <input
                className="input"
                placeholder="christmas, tribute, karaoke"
                value={config.ignore_keywords}
                onChange={e => update('ignore_keywords', e.target.value)}
              />
              <p className="text-[#444] text-xs mt-1">Comma-separated - artists matching any keyword will be skipped</p>
            </div>
            <div>
              <label className="label">Ignore Artists</label>
              <input
                className="input"
                placeholder="Artist One, Artist Two"
                value={config.ignore_artists}
                onChange={e => update('ignore_artists', e.target.value)}
              />
              <p className="text-[#444] text-xs mt-1">Comma-separated - these artists will never surface in results</p>
            </div>
            <div>
              <label className="label">Custom Build Name</label>
              <input
                className="input"
                placeholder="My Discovery"
                value={config.build_name_override}
                onChange={e => update('build_name_override', e.target.value)}
              />
              <p className="text-[#444] text-xs mt-1">Override the default build name shown in Builder</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 border-t border-[#1a1a1a] pt-5">
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <Toggle on={config.exclude_owned_artists} onChange={v => update('exclude_owned_artists', v)} />
                  <div>
                    <p className="text-text-primary text-sm font-medium">Exclude Owned Artists</p>
                    <p className="text-[#444] text-xs mt-0.5">Skip artists already present in your library</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Toggle on={config.avoid_repeat_tracks} onChange={v => update('avoid_repeat_tracks', v)} />
                  <div>
                    <p className="text-text-primary text-sm font-medium">Avoid Repeat Tracks</p>
                    <p className="text-[#444] text-xs mt-0.5">Prefer tracks not recently recommended</p>
                  </div>
                </div>
              </div>
              <div className="space-y-3">
                <div>
                  <label className="label">Track Repeat Cooldown (days)</label>
                  <input
                    type="number"
                    className="input"
                    min={1}
                    max={365}
                    value={config.track_repeat_cooldown_days}
                    onChange={e => update('track_repeat_cooldown_days', Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label">Discovery Cache TTL (days)</label>
                  <input
                    type="number"
                    className="input"
                    min={1}
                    max={365}
                    value={config.cache_ttl_days}
                    onChange={e => update('cache_ttl_days', Number(e.target.value))}
                  />
                </div>
              </div>
            </div>
            <div className="border-t border-[#1a1a1a] pt-5">
              <label className="label">Fetch Wait Timeout (seconds)</label>
              <input
                type="number"
                className="input max-w-[220px]"
                min={30}
                max={7200}
                value={config.fetch_wait_timeout_s}
                onChange={e => update('fetch_wait_timeout_s', Number(e.target.value))}
              />
              <p className="text-[#444] text-xs mt-1">Reserved for Build+Fetch orchestration flow.</p>
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
          <div className="border border-[#1a1a1a] bg-[#0d0d0d] p-4 space-y-3">
            <div className="w-full bg-[#1a1a1a] h-px">
              <div
                className="h-px bg-accent transition-all duration-300"
                style={{ width: `${pipelinePct}%` }}
              />
            </div>
            <p className="text-text-primary text-sm">
              {pipelineState.message || 'Running custom discovery...'}
            </p>
            <p className="text-[#555] text-xs">
              Stage: {pipelineState.stage || 'starting'}{' '}
              {pipelineState.total > 0 ? `(${pipelineState.processed}/${pipelineState.total})` : ''}
            </p>
          </div>
        </div>
      )}

      {!running && results !== null && (
        <section className="border-t border-[#1a1a1a] pt-8">
          <div className="flex items-center gap-2 mb-4">
            <Sparkles size={14} className="text-accent" />
            <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">
              {results.length} track{results.length !== 1 ? 's' : ''} compiled
            </span>
          </div>
          {results.length === 0 ? (
            <div className="py-12 text-center space-y-2">
              <p className="text-text-muted text-sm">No tracks matched these settings</p>
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
          <p className="text-[#444] text-sm">Configure and run to build a discovery track list</p>
        </div>
      )}
    </div>
  );
}

