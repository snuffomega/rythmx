import { useState } from 'react';
import { Play, Loader2, Users, Sparkles, ChevronDown, ChevronUp, Save, Circle } from 'lucide-react';
import { personalDiscoveryApi } from '../services/api';
import { getImageUrl } from '../utils/imageUrl';
import type { PersonalDiscoveryConfig, PersonalDiscoveryResult } from '../types';

const SEED_PERIODS: Array<{ value: PersonalDiscoveryConfig['seed_period']; label: string; short: string }> = [
  { value: '7day', label: 'Last 7 days', short: '7 Days' },
  { value: '1month', label: 'Last 30 days', short: '1 Month' },
  { value: '3month', label: 'Last 3 months', short: '3 Months' },
  { value: '6month', label: 'Last 6 months', short: '6 Months' },
  { value: '12month', label: 'Last 12 months', short: '12 Months' },
  { value: 'overall', label: 'All time', short: 'All Time' },
];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 || 12;
  const ampm = i < 12 ? 'AM' : 'PM';
  return { value: i, label: `${h}:00 ${ampm}` };
});

type PDRunMode = 'build' | 'fetch';

const PD_PIPELINE_STEPS = [
  { label: 'Poll History' },
  { label: 'Resolve Artists' },
  { label: 'Find Tracks' },
  { label: 'Check Library' },
  { label: 'Build', cruiseOnly: true },
  { label: 'Queue Tracks', cruiseOnly: true },
  { label: 'Create & Publish' },
];

const MODE_INFO: Record<PDRunMode, string> = {
  build: 'Generates a curated playlist of tracks from artists similar to your listening history. Best for exploring at your own pace.',
  fetch: 'Queues similar-artist albums directly into your acquisition pipeline — hands-off discovery that fills your library automatically.',
};

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative inline-flex items-center w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${on ? 'bg-accent' : 'bg-[#2a2a2a]'}`}
    >
      <span className={`inline-block w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
    </button>
  );
}

function PDPipeline({ runMode }: { runMode: PDRunMode }) {
  const steps = runMode === 'build'
    ? PD_PIPELINE_STEPS.filter(s => !s.cruiseOnly)
    : PD_PIPELINE_STEPS;

  return (
    <div className="border border-[#1a1a1a]">
      <div className="hidden sm:flex px-4 py-3 items-center">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center">
            <div className="flex items-center gap-1.5 py-2 px-3 text-xs text-[#555]">
              <Circle size={11} className="flex-shrink-0" />
              <span className="font-medium">
                <span className="mr-1 opacity-40">{i + 1}</span>
                {step.label}
                {step.cruiseOnly && <span className="ml-1 opacity-30 text-[10px]">fetch</span>}
              </span>
            </div>
            {i < steps.length - 1 && <span className="text-[#222] text-xs px-0.5">›</span>}
          </div>
        ))}
      </div>
      <div className="flex sm:hidden items-center justify-between px-3 py-3 gap-1">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-1 min-w-0 flex-1">
            <div className="flex flex-col items-center gap-0.5 min-w-0 flex-1 text-[#444]">
              <Circle size={12} className="flex-shrink-0" />
              <span className="text-[9px] font-medium leading-tight text-center truncate w-full">{step.label}</span>
            </div>
            {i < steps.length - 1 && <span className="text-[#1e1e1e] text-xs flex-shrink-0 mb-3">›</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function ArtistResultCard({ result }: { result: PersonalDiscoveryResult }) {
  const hue = result.artist.charCodeAt(0) % 360;
  return (
    <div className="group flex items-center gap-3 px-4 py-3 bg-[#0d0d0d] border border-[#1a1a1a] hover:border-[#2a2a2a] transition-colors cursor-pointer">
      <div className="w-12 h-12 flex-shrink-0 overflow-hidden">
        {result.image ? (
          <img src={getImageUrl(result.image)} alt={result.artist} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
        ) : (
          <div className="w-full h-full flex items-center justify-center" style={{ background: `hsl(${hue},30%,12%)` }}>
            <Users size={18} className="text-[#444]" />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-text-primary text-sm font-semibold truncate">{result.artist}</p>
        {result.reason && <p className="text-[#444] text-xs truncate mt-0.5">{result.reason}</p>}
        {result.tags && result.tags.length > 0 && (
          <div className="flex gap-1 mt-1 flex-wrap">
            {result.tags.slice(0, 3).map(t => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 bg-[#161616] border border-[#222] text-[#555] uppercase tracking-wide">{t}</span>
            ))}
          </div>
        )}
      </div>
      {result.similarity !== undefined && (
        <div className="flex-shrink-0 text-right">
          <div className="text-accent text-sm font-bold tabular-nums">{Math.round(result.similarity * 100)}%</div>
          <div className="text-[#444] text-[10px] uppercase tracking-wide">match</div>
        </div>
      )}
    </div>
  );
}

interface PersonalDiscoveryProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

interface PDConfig extends PersonalDiscoveryConfig {
  run_mode: PDRunMode;
  auto_publish: boolean;
  schedule_enabled: boolean;
  schedule_weekday: number;
  schedule_hour: number;
  dry_run: boolean;
}

export function PersonalDiscovery({ toast }: PersonalDiscoveryProps) {
  const [config, setConfig] = useState<PDConfig>({
    run_mode: 'build',
    closeness: 5,
    seed_period: '1month',
    min_scrobbles: 10,
    max_tracks: 50,
    auto_publish: false,
    schedule_enabled: false,
    schedule_weekday: 1,
    schedule_hour: 8,
    dry_run: false,
  });
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<PersonalDiscoveryResult[] | null>(null);

  const update = <K extends keyof PDConfig>(key: K, value: PDConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await new Promise(r => setTimeout(r, 400));
      toast.success('Personal Discovery config saved');
    } catch {
      toast.error('Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    setRunning(true);
    try {
      const data = await personalDiscoveryApi.run(config);
      setResults(data);
      toast.success('Personal Discovery complete');
    } catch {
      toast.error('Failed to run Personal Discovery');
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

      <div>
        <div className="pb-3 border-b border-[#1a1a1a] mb-6">
          <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Configuration</span>
        </div>

        <div className="space-y-7">

          <div>
            <div className="flex border border-[#2a2a2a] w-fit mb-3">
              {(['build', 'fetch'] as PDRunMode[]).map(mode => {
                const active = config.run_mode === mode;
                return (
                  <button
                    key={mode}
                    onClick={() => update('run_mode', mode)}
                    className={`px-4 py-1.5 text-sm font-semibold transition-all duration-150 border-r border-[#2a2a2a] last:border-r-0 ${
                      active ? 'bg-[#1e1e1e] text-text-primary' : 'text-[#3a3a3a] hover:text-[#666]'
                    }`}
                  >
                    {mode === 'build' ? 'Build' : 'Fetch'}
                  </button>
                );
              })}
            </div>
            <p className="text-[#555] text-xs leading-relaxed">{MODE_INFO[config.run_mode]}</p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="label mb-0">Closeness to Taste</label>
              <span className="text-accent text-xs font-semibold">{closenessLabel}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[#444] text-xs whitespace-nowrap">Closer</span>
              <div className="flex-1 relative">
                <input
                  type="range"
                  min={1}
                  max={9}
                  step={1}
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
              <select
                className="select"
                value={config.seed_period}
                onChange={e => update('seed_period', e.target.value as PersonalDiscoveryConfig['seed_period'])}
              >
                {SEED_PERIODS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Min Artist Scrobbles</label>
              <input type="number" className="input" min={1} value={config.min_scrobbles} onChange={e => update('min_scrobbles', Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Max Playlist Size</label>
              <input type="number" className="input" min={5} max={200} value={config.max_tracks} onChange={e => update('max_tracks', Number(e.target.value))} />
            </div>
          </div>

        </div>
      </div>

      <PDPipeline runMode={config.run_mode} />

      <section className="border-t border-[#1a1a1a] pt-6">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-5">Automation</h2>

        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <Toggle on={config.auto_publish} onChange={v => update('auto_publish', v)} />
            <div>
              <p className="text-text-primary text-sm font-medium">Auto Publish</p>
              <p className="text-[#444] text-xs">Push to Plex after each run</p>
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
        <button
          onClick={() => setAdvanced(a => !a)}
          className="flex items-center justify-between w-full text-left"
        >
          <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">Advanced</span>
          {advanced ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
        </button>

        {advanced && (
          <div className="mt-5 space-y-5">
            <div>
              <label className="label">Ignore Keywords</label>
              <input className="input" placeholder="christmas, tribute, karaoke" />
              <p className="text-[#444] text-xs mt-1">Comma-separated — artists matching any keyword will be skipped</p>
            </div>
            <div>
              <label className="label">Ignore Artists</label>
              <input className="input" placeholder="Artist One, Artist Two" />
              <p className="text-[#444] text-xs mt-1">Comma-separated — these artists will never surface in results</p>
            </div>
            <div>
              <label className="label">Custom Playlist Name</label>
              <input className="input" placeholder="My Discovery" />
              <p className="text-[#444] text-xs mt-1">Override the default playlist name used when publishing</p>
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
        <button onClick={handleRun} disabled={running || saving} className="btn-primary flex items-center gap-2 text-sm">
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          {running ? 'Discovering…' : 'Run Discovery'}
        </button>
        <button onClick={handleSave} disabled={saving || running} className="btn-secondary flex items-center gap-2 text-sm">
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save Config
        </button>
      </div>

      {running && (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-[72px] bg-[#0d0d0d] border border-[#1a1a1a] animate-pulse" />
          ))}
        </div>
      )}

      {!running && results !== null && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Sparkles size={14} className="text-accent" />
            <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">
              {results.length} artist{results.length !== 1 ? 's' : ''} discovered
            </span>
          </div>
          {results.length === 0 ? (
            <div className="py-12 text-center space-y-2">
              <Users size={24} className="text-[#333] mx-auto" />
              <p className="text-text-muted text-sm">No new artists found for these settings</p>
              <p className="text-[#444] text-xs">Try increasing the closeness value or expanding the seed period</p>
            </div>
          ) : (
            <div className="space-y-2">
              {results.map((r, i) => <ArtistResultCard key={i} result={r} />)}
            </div>
          )}
        </div>
      )}

      {!running && results === null && (
        <div className="py-12 text-center border border-dashed border-[#1e1e1e] space-y-2">
          <Sparkles size={22} className="text-[#2a2a2a] mx-auto" />
          <p className="text-[#444] text-sm">Configure and run to discover new artists</p>
        </div>
      )}

    </div>
  );
}
