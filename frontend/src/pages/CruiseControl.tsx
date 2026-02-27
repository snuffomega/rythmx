import { useState, useEffect } from 'react';
import { Zap, Save, ChevronDown, ChevronUp, RotateCcw, CheckCircle, AlertCircle, Loader2, Circle, Sparkles } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { cruiseControlApi, releaseCacheApi } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { RowSkeleton } from '../components/Skeleton';
import { PersonalDiscovery } from './PersonalDiscovery';
import type { CruiseControlConfig, Period } from '../types';

type CCTab = 'new-music' | 'personal-discovery';

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

const PIPELINE_STEPS = [
  { label: 'Poll History' },
  { label: 'Resolve Artists' },
  { label: 'Find New Releases' },
  { label: 'Check Library' },
  { label: 'Build', cruiseOnly: true },
  { label: 'Queue Tracks', cruiseOnly: true },
  { label: 'Create & Publish' },
];

function PipelineStatus({ stage, totalStages, state, runMode }: {
  stage?: number;
  totalStages?: number;
  state: 'idle' | 'running' | 'error' | 'completed';
  runMode: 'playlist' | 'cruise';
}) {
  const steps = runMode === 'playlist'
    ? PIPELINE_STEPS.filter(s => !s.cruiseOnly)
    : PIPELINE_STEPS;

  return (
    <div className="border border-[#1a1a1a]">
      {/* Desktop: inline row */}
      <div className="hidden sm:flex px-4 py-3 items-center">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center">
              <div className={`flex items-center gap-1.5 py-2 px-3 text-xs transition-colors ${
                isError ? 'text-danger' : isActive ? 'text-white' : isDone ? 'text-[#555]' : 'text-[#555]'
              }`}>
                {isError ? (
                  <AlertCircle size={11} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={11} className="animate-spin flex-shrink-0" />
                ) : isDone ? (
                  <CheckCircle size={11} className="flex-shrink-0" />
                ) : (
                  <Circle size={11} className="flex-shrink-0" />
                )}
                <span className={`font-medium ${isActive ? 'font-semibold' : ''}`}>
                  <span className="mr-1 opacity-40">{stepNum}</span>
                  {step.label}
                  {step.cruiseOnly && <span className="ml-1 opacity-30 text-[10px]">cruise</span>}
                </span>
              </div>
              {i < steps.length - 1 && (
                <span className="text-[#222] text-xs px-0.5">›</span>
              )}
            </div>
          );
        })}
      </div>

      {/* Mobile: compact grid of step bubbles */}
      <div className="flex sm:hidden items-center justify-between px-3 py-3 gap-1">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center gap-1 min-w-0 flex-1">
              <div className={`flex flex-col items-center gap-0.5 min-w-0 flex-1 ${
                isError ? 'text-danger' : isActive ? 'text-white' : 'text-[#555]'
              }`}>
                {isError ? (
                  <AlertCircle size={12} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={12} className="animate-spin flex-shrink-0" />
                ) : (
                  <Circle size={12} className="flex-shrink-0" />
                )}
                <span className="text-[9px] font-medium leading-tight text-center truncate w-full">{step.label}</span>
              </div>
              {i < steps.length - 1 && (
                <span className="text-[#1e1e1e] text-xs flex-shrink-0 mb-3">›</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LastRunResults({ data }: { data: import('../types').CruiseControlStatus }) {
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

interface CruiseControlProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function CruiseControl({ toast }: CruiseControlProps) {
  const { data: config, loading: configLoading } = useApi(() => cruiseControlApi.getConfig());
  const { data: history, loading: historyLoading } = useApi(() => cruiseControlApi.getHistory());
  const { data: statusData, refetch: refetchStatus } = useApi(() => cruiseControlApi.getStatus());

  const [tab, setTab] = useState<CCTab>('new-music');
  const [form, setForm] = useState<Partial<CruiseControlConfig>>({});
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  // Poll status every 2s while a run is active
  useEffect(() => {
    if (statusData?.state === 'running') {
      const interval = setInterval(refetchStatus, 2000);
      return () => clearInterval(interval);
    }
  }, [statusData?.state, refetchStatus]);

  const update = (key: keyof CruiseControlConfig, value: unknown) =>
    setForm(f => ({ ...f, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await cruiseControlApi.saveConfig(form);
      toast.success('Config saved');
    } catch {
      toast.error('Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndRun = async () => {
    setSaving(true);
    try {
      await cruiseControlApi.saveConfig(form);
      const runMode = form.cc_dry_run ? 'dry' : (form.cc_run_mode ?? 'playlist');
      await cruiseControlApi.runNow(runMode);
      toast.success('Config saved and run started');
      refetchStatus();
    } catch {
      toast.error('Failed to save and run');
    } finally {
      setSaving(false);
    }
  };

  const handleClearCache = async () => {
    try {
      await releaseCacheApi.clear();
      toast.success('Release cache cleared');
    } catch {
      toast.error('Failed to clear cache');
    }
    setConfirmClear(false);
  };

  return (
    <div className="py-8 space-y-8">
      <div className="space-y-6">
        <div>
          <h1 className="page-title">Cruise Control</h1>
          <p className="text-text-muted text-sm mt-1">Automate and find the music you love</p>
        </div>

        <div className="flex items-center gap-1.5 bg-[#0e0e0e] border border-[#1a1a1a] p-1.5 w-fit">
          <button
            onClick={() => setTab('new-music')}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 ${
              tab === 'new-music'
                ? 'bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]'
                : 'text-[#3a3a3a] hover:text-[#666]'
            }`}
          >
            <Zap size={14} className={tab === 'new-music' ? 'text-accent' : ''} />
            New Music
          </button>
          <button
            onClick={() => setTab('personal-discovery')}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 ${
              tab === 'personal-discovery'
                ? 'bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]'
                : 'text-[#3a3a3a] hover:text-[#666]'
            }`}
          >
            <Sparkles size={14} className={tab === 'personal-discovery' ? 'text-accent' : ''} />
            Personal Discovery
          </button>
        </div>
      </div>

      {tab === 'personal-discovery' && (
        <PersonalDiscovery toast={toast} />
      )}

      {tab === 'new-music' && <>

      {configLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-10 bg-[#1c1c1c] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-8">

          <div>
            <div className="pb-3 border-b border-[#1a1a1a] mb-6">
              <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Configuration</span>
            </div>

            <div className="space-y-7">

              <div>
                <div className="flex border border-[#2a2a2a] w-fit mb-3">
                  {([
                    { value: 'playlist', label: 'Playlist' },
                    { value: 'cruise', label: 'Cruise' },
                  ] as const).map(({ value, label }) => {
                    const active = (form.cc_run_mode ?? 'playlist') === value;
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
                  {(form.cc_run_mode ?? 'playlist') === 'playlist'
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

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <label className="label">Scrobbling Period</label>
                  <select className="select" value={form.cc_period ?? '1month'} onChange={e => update('cc_period', e.target.value)}>
                    {PERIOD_LABELS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label">Min Artist Scrobbles</label>
                  <input
                    type="number"
                    className="input"
                    value={form.cc_min_listens ?? ''}
                    onChange={e => update('cc_min_listens', Number(e.target.value))}
                    min={1}
                  />
                </div>
                <div>
                  <label className="label">Lookback Days</label>
                  <input
                    type="number"
                    className="input"
                    value={form.cc_lookback_days ?? ''}
                    onChange={e => update('cc_lookback_days', Number(e.target.value))}
                    min={1}
                  />
                </div>
                <div>
                  <label className="label">Max Per Cycle</label>
                  <input
                    type="number"
                    className="input"
                    value={form.cc_max_per_cycle ?? ''}
                    onChange={e => update('cc_max_per_cycle', Number(e.target.value))}
                    min={1}
                  />
                </div>
              </div>

            </div>
          </div>

          <PipelineStatus
            stage={statusData?.stage}
            totalStages={statusData?.total_stages}
            state={statusData?.state ?? 'idle'}
            runMode={form.cc_run_mode ?? 'playlist'}
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
                  <label className="label">Playlist Name</label>
                  <input
                    className="input"
                    value={form.cc_playlist_prefix ?? ''}
                    onChange={e => update('cc_playlist_prefix', e.target.value)}
                    placeholder="New Music"
                  />
                  <p className="text-[#444] text-xs mt-1">CC appends the date — e.g. "New Music — Jan 2026"</p>
                </div>
                <div>
                  <label className="label">Ignore Keywords</label>
                  <input
                    className="input"
                    value={form.nr_ignore_keywords ?? ''}
                    onChange={e => update('nr_ignore_keywords', e.target.value)}
                    placeholder="christmas, karaoke, tribute, remaster"
                  />
                  <p className="text-[#444] text-xs mt-1">Comma-separated — releases matching any keyword will be skipped</p>
                </div>
                <div>
                  <label className="label">Ignore Artists</label>
                  <input
                    className="input"
                    value={form.nr_ignore_artists ?? ''}
                    onChange={e => update('nr_ignore_artists', e.target.value)}
                    placeholder="dmx, i voted for kodos, funkmaster flex"
                  />
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
                    <button onClick={() => setConfirmClear(true)} className="btn-secondary text-sm flex items-center gap-2 flex-shrink-0">
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
            <button
              onClick={handleSaveAndRun}
              disabled={saving || statusData?.state === 'running'}
              className="btn-primary flex items-center gap-2 text-sm"
            >
              {(saving || statusData?.state === 'running') ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
              Save & Run
            </button>
            <button onClick={handleSave} disabled={saving} className="btn-secondary flex items-center gap-2 text-sm">
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              Save Config
            </button>
          </div>

          {statusData && <LastRunResults data={statusData} />}

        </div>
      )}

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Last Run History</h2>
        {historyLoading ? (
          <div className="space-y-0">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="border-b border-[#1a1a1a]"><RowSkeleton /></div>
            ))}
          </div>
        ) : !history || history.length === 0 ? (
          <p className="text-text-muted text-sm py-8">No history yet — run Cruise Control to get started</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[#1a1a1a]">
                  <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-0 py-3">Artist</th>
                  <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3">Album</th>
                  <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3">Status</th>
                  <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3 hidden sm:table-cell">Date</th>
                </tr>
              </thead>
              <tbody>
                {history.map((item, i) => (
                  <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#141414] transition-colors">
                    <td className="px-0 py-3 text-text-primary font-medium">{item.artist}</td>
                    <td className="px-4 py-3 text-text-secondary">{item.album}</td>
                    <td className="px-4 py-3"><StatusBadge status={item.status} /></td>
                    <td className="px-4 py-3 text-[#444] hidden sm:table-cell">
                      {new Date(item.date).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmClear}
        title="Clear Release Cache?"
        description="This will remove all cached release data. Cruise Control will re-fetch everything on its next run."
        confirmLabel="Clear Cache"
        danger
        onConfirm={handleClearCache}
        onCancel={() => setConfirmClear(false)}
      />
      </>}
    </div>
  );
}
