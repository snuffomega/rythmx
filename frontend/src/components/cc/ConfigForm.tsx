import { Zap, Save, ChevronDown, ChevronUp, RotateCcw, Loader2 } from 'lucide-react';
import { Toggle } from '../common';
import { PipelineStatus } from './Pipeline';
import type { CruiseControlConfig, CruiseControlStatus, Period } from '../../types';

const PERIOD_LABELS: Array<{ value: Period; label: string }> = [
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

function LastRunResults({ data }: { data: CruiseControlStatus }) {
  const stats = [
    { label: 'Last run', value: data.last_run ? new Date(data.last_run).toLocaleString() : 'Never' },
    { label: 'Releases found', value: data.summary?.new_releases ?? '—' },
    { label: 'Already owned', value: data.summary?.owned ?? '—' },
    { label: 'Queued', value: data.summary?.queued ?? '—' },
    { label: 'Artists checked', value: data.summary?.artists_checked ?? '—' },
  ];
  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a]">
      <div className="px-4 py-3 border-b border-[#1a1a1a]">
        <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Last Run Results</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 divide-x divide-[#1a1a1a]">
        {stats.map(({ label, value }) => (
          <div key={label} className="px-4 py-3">
            <div className="text-[#444] text-[10px] uppercase tracking-widest mb-1">{label}</div>
            <div className="text-text-secondary text-sm font-medium truncate">{String(value)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface CCConfigFormProps {
  form: Partial<CruiseControlConfig>;
  update: (key: keyof CruiseControlConfig, value: unknown) => void;
  advanced: boolean;
  onAdvancedToggle: () => void;
  saving: boolean;
  onSave: () => void;
  onSaveAndRun: () => void;
  onRequestClearCache: () => void;
  statusData: CruiseControlStatus | null;
}

export function CCConfigForm({
  form, update, advanced, onAdvancedToggle,
  saving, onSave, onSaveAndRun, onRequestClearCache, statusData,
}: CCConfigFormProps) {
  const isRunning = statusData?.state === 'running';

  return (
    <div className="space-y-8">
      <div>
        <div className="pb-3 border-b border-[#1a1a1a] mb-6">
          <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Configuration</span>
        </div>

        <div className="space-y-7">
          <div>
            <div className="flex border border-[#2a2a2a] w-fit mb-3">
              {([
                { value: 'build', label: 'Build' },
                { value: 'fetch', label: 'Fetch' },
              ] as const).map(({ value, label }) => {
                const active = (form.cc_run_mode ?? 'build') === value;
                return (
                  <button
                    key={value}
                    onClick={() => update('cc_run_mode', value)}
                    className={`px-4 py-1.5 text-sm font-semibold transition-all duration-150 border-r border-[#2a2a2a] last:border-r-0 ${
                      active ? 'bg-[#1e1e1e] text-text-primary' : 'text-[#3a3a3a] hover:text-[#666]'
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <p className="text-[#555] text-xs leading-relaxed">
              {(form.cc_run_mode ?? 'build') === 'build'
                ? 'Creates a curated playlist of new releases from artists in your library. Browse at your own pace.'
                : 'Queues new releases directly into your acquisition pipeline for hands-off library growth.'}
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="label mb-0">Scrobbling Period</label>
              <span className="text-accent text-xs font-semibold">
                {PERIOD_LABELS.find(p => p.value === (form.cc_period ?? '1month'))?.label ?? ''}
              </span>
            </div>
            <div className="flex items-center gap-3 mb-1">
              <span className="text-[#444] text-xs whitespace-nowrap">7 Days</span>
              <div className="flex-1 relative">
                <input
                  type="range"
                  min={0}
                  max={5}
                  step={1}
                  value={PERIOD_LABELS.findIndex(p => p.value === (form.cc_period ?? '1month'))}
                  onChange={e => update('cc_period', PERIOD_LABELS[Number(e.target.value)].value)}
                  className="w-full accent-accent h-1.5"
                />
                <div className="flex justify-between text-[#2a2a2a] text-[10px] mt-0.5">
                  {PERIOD_LABELS.map(p => (
                    <span key={p.value} className={`w-px text-center ${(form.cc_period ?? '1month') === p.value ? 'text-accent' : ''}`}>|</span>
                  ))}
                </div>
              </div>
              <span className="text-[#444] text-xs whitespace-nowrap">All Time</span>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            <div>
              <label className="label">Scrobbling Period</label>
              <select className="select" value={form.cc_period ?? '1month'} onChange={e => update('cc_period', e.target.value)}>
                {PERIOD_LABELS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Min Artist Scrobbles</label>
              <input type="number" className="input" value={form.cc_min_listens ?? ''} onChange={e => update('cc_min_listens', Number(e.target.value))} min={1} />
            </div>
            <div>
              <label className="label">Lookback Days</label>
              <input type="number" className="input" value={form.cc_lookback_days ?? ''} onChange={e => update('cc_lookback_days', Number(e.target.value))} min={1} />
            </div>
            <div>
              <label className="label">Max Per Cycle</label>
              <input type="number" className="input" value={form.cc_max_per_cycle ?? ''} onChange={e => update('cc_max_per_cycle', Number(e.target.value))} min={1} />
            </div>
            <div>
              <label className="label">Max Playlist Tracks</label>
              <input type="number" className="input" value={form.cc_max_playlist_tracks ?? ''} onChange={e => update('cc_max_playlist_tracks', Number(e.target.value))} min={10} max={500} />
            </div>
          </div>
        </div>
      </div>

      <PipelineStatus
        stage={statusData?.stage}
        totalStages={statusData?.total_stages}
        state={statusData?.state ?? 'idle'}
        runMode={form.cc_run_mode ?? 'build'}
      />

      <section className="border-t border-[#1a1a1a] pt-6">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-5">Automation</h2>
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <Toggle on={!!form.cc_auto_push_playlist} onChange={v => update('cc_auto_push_playlist', v)} />
            <div>
              <p className="text-text-primary text-sm font-medium">Auto Publish</p>
              <p className="text-[#444] text-xs">Push to Plex after each run</p>
            </div>
          </div>
          <div className="w-px h-8 bg-[#1a1a1a] self-center" />
          <div className="flex items-center gap-3">
            <Toggle on={!!form.cc_enabled} onChange={v => update('cc_enabled', v)} />
            <div>
              <p className="text-text-primary text-sm font-medium">Weekly Schedule</p>
              <p className="text-[#444] text-xs">Run on a set day and time</p>
            </div>
            <select className="select !w-auto ml-2" value={form.cc_schedule_weekday ?? 1} onChange={e => update('cc_schedule_weekday', Number(e.target.value))}>
              {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select>
            <select className="select !w-auto" value={form.cc_schedule_hour ?? 8} onChange={e => update('cc_schedule_hour', Number(e.target.value))}>
              {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </div>
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-6">
        <button onClick={onAdvancedToggle} className="flex items-center justify-between w-full text-left">
          <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">Advanced</span>
          {advanced ? <ChevronUp size={14} className="text-text-muted" /> : <ChevronDown size={14} className="text-text-muted" />}
        </button>

        {advanced && (
          <div className="mt-5 space-y-5">
            <div>
              <label className="label">Playlist Name</label>
              <input className="input" value={form.cc_playlist_prefix ?? ''} onChange={e => update('cc_playlist_prefix', e.target.value)} placeholder="New Music" />
              <p className="text-[#444] text-xs mt-1">CC appends the date — e.g. "New Music — Jan 2026"</p>
            </div>
            <div>
              <label className="label">Ignore Keywords</label>
              <input className="input" value={form.nr_ignore_keywords ?? ''} onChange={e => update('nr_ignore_keywords', e.target.value)} placeholder="christmas, karaoke, tribute, remaster" />
              <p className="text-[#444] text-xs mt-1">Comma-separated — releases matching any keyword will be skipped</p>
            </div>
            <div>
              <label className="label">Ignore Artists</label>
              <input className="input" value={form.nr_ignore_artists ?? ''} onChange={e => update('nr_ignore_artists', e.target.value)} placeholder="dmx, i voted for kodos, funkmaster flex" />
              <p className="text-[#444] text-xs mt-1">Comma-separated — these artists will be skipped entirely</p>
            </div>

            <div className="border-t border-[#1a1a1a] pt-5 flex items-center gap-6">
              <div className="flex items-center gap-2">
                <div>
                  <p className="text-text-primary text-sm font-medium">New Release Scheduled Refresh</p>
                  <p className="text-[#444] text-xs mt-0.5">Weekly refresh of new albums and releases</p>
                </div>
                <select className="select !w-auto" value={form.release_cache_refresh_weekday ?? 4} onChange={e => update('release_cache_refresh_weekday', Number(e.target.value))}>
                  {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                </select>
                <select className="select !w-auto" value={form.release_cache_refresh_hour ?? 6} onChange={e => update('release_cache_refresh_hour', Number(e.target.value))}>
                  {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                </select>
              </div>
              <div className="flex items-center gap-2 ml-auto">
                <div>
                  <p className="text-text-primary text-sm font-medium">Refresh Release Data Now</p>
                  <p className="text-[#444] text-xs mt-0.5">Clear and rebuild the release cache immediately</p>
                </div>
                <button onClick={onRequestClearCache} className="btn-secondary text-sm flex items-center gap-2 flex-shrink-0">
                  <RotateCcw size={13} /> Force Refresh
                </button>
              </div>
            </div>

            <div className="border-t border-[#1a1a1a] pt-5 flex items-center gap-3">
              <Toggle on={!!form.cc_dry_run} onChange={v => update('cc_dry_run', v)} />
              <div>
                <p className="text-text-primary text-sm font-medium">Dry Run</p>
                <p className="text-[#444] text-xs mt-0.5">Simulate a run without making any changes</p>
              </div>
            </div>
          </div>
        )}
      </section>

      <div className="flex gap-3 pt-2 border-t border-[#1a1a1a]">
        <button onClick={onSaveAndRun} disabled={saving || isRunning} className="btn-primary flex items-center gap-2 text-sm">
          {(saving || isRunning) ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
          Save & Run
        </button>
        <button onClick={onSave} disabled={saving} className="btn-secondary flex items-center gap-2 text-sm">
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          Save Config
        </button>
      </div>

      {statusData && <LastRunResults data={statusData} />}
    </div>
  );
}
