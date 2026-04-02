import { useState, useEffect, useRef } from 'react';
import { Settings, Zap, ChevronDown, ChevronUp, Music2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { forgeBuildsApi, forgeNewMusicApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import { Toggle } from '../components/common';
import type { NewMusicConfig, DiscoveredRelease } from '../types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PERIODS = [
  { value: '7day', label: '7 days' },
  { value: '1month', label: '1 month' },
  { value: '3month', label: '3 months' },
  { value: '6month', label: '6 months' },
  { value: '12month', label: '12 months' },
  { value: 'overall', label: 'All time' },
] as const;

const LOOKBACK_OPTIONS = [
  { value: 30, label: '30 days' },
  { value: 60, label: '60 days' },
  { value: 90, label: '90 days' },
  { value: 180, label: '6 months' },
];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 || 12;
  const ampm = i < 12 ? 'AM' : 'PM';
  return { value: i, label: `${h}:00 ${ampm}` };
});

const DEFAULT_CONFIG: NewMusicConfig = {
  nm_min_scrobbles: 10,
  nm_period: '1month',
  nm_lookback_days: 90,
  nm_match_mode: 'loose',
  nm_ignore_keywords: '',
  nm_ignore_artists: '',
  nm_release_kinds: 'album_preferred',
  nm_schedule_enabled: false,
  nm_schedule_weekday: 1,
  nm_schedule_hour: 8,
};

// Pipeline stages for progress display
const STAGES = [
  { key: 'history', label: 'Reading your listening history', pct: 10 },
  { key: 'releases', label: 'Finding new releases from your listening history', pct: 90 },
  { key: 'done', label: 'Done', pct: 100 },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ForgeNewMusic() {
  const navigate = useNavigate();
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);

  const [config, setConfig] = useState<NewMusicConfig>(DEFAULT_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const [running, setRunning] = useState(false);
  const [stageIdx, setStageIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [results, setResults] = useState<DiscoveredRelease[] | null>(null);
  const [runSummary, setRunSummary] = useState<{ releases_found: number; nm_period: string; nm_lookback_days: number } | null>(null);

  const update = <K extends keyof NewMusicConfig>(key: K, value: NewMusicConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }));

  // Load config + existing results on mount
  useEffect(() => {
    forgeNewMusicApi.getConfig()
      .then(cfg => { setConfig(cfg); setConfigLoaded(true); })
      .catch(() => { setConfigLoaded(true); });

    forgeNewMusicApi.getResults()
      .then(releases => {
        if (releases.length > 0) setResults(releases);
      })
      .catch(() => {});
  }, []);

  // Clean up timer on unmount
  useEffect(() => () => {
    if (progressTimerRef.current) clearInterval(progressTimerRef.current);
  }, []);

  // ---------------------------------------------------------------------------
  // Progress animation
  // ---------------------------------------------------------------------------

  const startProgress = () => {
    setStageIdx(0);
    setProgress(0);

    let currentPct = 0;
    let currentStage = 0;

    progressTimerRef.current = setInterval(() => {
      const targetPct = STAGES[currentStage]?.pct ?? 90;
      if (currentPct < targetPct - 2) {
        currentPct += 1;
        setProgress(currentPct);
      } else if (currentStage < STAGES.length - 2) {
        currentStage += 1;
        setStageIdx(currentStage);
      }
    }, 300);
  };

  const finishProgress = () => {
    if (progressTimerRef.current) {
      clearInterval(progressTimerRef.current);
      progressTimerRef.current = null;
    }
    setStageIdx(STAGES.length - 1);
    setProgress(100);
  };

  // ---------------------------------------------------------------------------
  // Run handler
  // ---------------------------------------------------------------------------

  const handleRun = async () => {
    setRunning(true);
    setResults(null);
    setEditOpen(false);
    startProgress();

    // Auto-save config before run (non-fatal)
    await forgeNewMusicApi.saveConfig(config).catch(() => {});

    try {
      const data = await forgeNewMusicApi.run(config);
      finishProgress();
      setResults(data.releases);
      setRunSummary({
        releases_found: data.releases_found,
        nm_period: config.nm_period,
        nm_lookback_days: config.nm_lookback_days,
      });

      let queued = false;
      try {
        const stamp = new Date().toISOString().slice(0, 16).replace('T', ' ');
        await forgeBuildsApi.create({
          name: `New Music ${stamp}`,
          source: 'new_music',
          status: 'ready',
          run_mode: 'build',
          track_list: data.releases as unknown as Array<Record<string, unknown>>,
          summary: {
            artists_checked: data.artists_checked,
            releases_found: data.releases_found,
            nm_period: config.nm_period,
            nm_lookback_days: config.nm_lookback_days,
          },
        });
        queued = true;
      } catch {
        toastError('Run completed, but build queueing failed');
      }

      toastSuccess(
        queued
          ? `${data.releases_found} releases found and queued in Builder`
          : `${data.releases_found} releases found`
      );

      // Navigate to Builder after brief done-state
      setTimeout(() => {
        navigate({ to: '/forge/builder' });
      }, 900);
    } catch {
      finishProgress();
      toastError('Pipeline failed — check logs');
      setRunning(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Sub-components
  // ---------------------------------------------------------------------------

  const ChipRow = <T extends string | number>({
    options, value, onSelect,
  }: { options: { value: T; label: string }[]; value: T; onSelect: (v: T) => void }) => (
    <div className="flex flex-wrap gap-1.5">
      {options.map(opt => (
        <button
          key={String(opt.value)}
          onClick={() => onSelect(opt.value)}
          className={`px-3 py-1.5 text-xs font-semibold border transition-colors ${
            opt.value === value
              ? 'bg-accent text-black border-accent'
              : 'text-[#555] border-[#222] hover:border-[#333] hover:text-[#888]'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );

  // ---------------------------------------------------------------------------
  // Config fields (shared between first-run panel and edit panel)
  // ---------------------------------------------------------------------------

  const configFields = (
    <div className="space-y-6">
      {/* Release window — the primary setting */}
      <div>
        <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
          Release window
        </label>
        <ChipRow
          options={LOOKBACK_OPTIONS}
          value={config.nm_lookback_days}
          onSelect={v => update('nm_lookback_days', v)}
        />
        <p className="text-[#444] text-[11px] mt-1.5">How far back to look for new releases.</p>
      </div>

      {/* Advanced section */}
      <div>
        <button
          onClick={() => setAdvancedOpen(v => !v)}
          className="flex items-center gap-1.5 text-[#444] hover:text-[#666] text-xs font-semibold uppercase tracking-widest transition-colors"
        >
          {advancedOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          Advanced
        </button>

        {advancedOpen && (
          <div className="mt-5 space-y-6 border-l border-[#1a1a1a] pl-4">
            {/* Listening period */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Listening period
              </label>
              <ChipRow
                options={PERIODS as unknown as { value: string; label: string }[]}
                value={config.nm_period}
                onSelect={v => update('nm_period', v as NewMusicConfig['nm_period'])}
              />
              <p className="text-[#444] text-[11px] mt-1.5">How far back in your listening history to look for seed artists.</p>
            </div>

            {/* Min listens */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Min listens
              </label>
              <input
                type="number"
                min={1}
                max={500}
                value={config.nm_min_scrobbles}
                onChange={e => update('nm_min_scrobbles', parseInt(e.target.value) || 1)}
                className="w-20 bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
              />
              <p className="text-[#444] text-[11px] mt-1">Minimum plays to qualify an artist as a seed.</p>
            </div>

            {/* Release types */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Release types
              </label>
              <ChipRow
                options={[
                  { value: 'all', label: 'All' },
                  { value: 'album_preferred', label: 'Album preferred' },
                  { value: 'album', label: 'Album only' },
                ]}
                value={config.nm_release_kinds}
                onSelect={v => update('nm_release_kinds', v as NewMusicConfig['nm_release_kinds'])}
              />
              <p className="text-[#444] text-[11px] mt-1.5">
                {config.nm_release_kinds === 'album_preferred'
                  ? 'Shows the album version when available; falls back to singles/EPs if no album exists.'
                  : config.nm_release_kinds === 'album'
                    ? 'Only albums. Artists with no album in the window are hidden.'
                    : 'All release types.'}
              </p>
            </div>

            {/* Match mode */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Match mode
              </label>
              <ChipRow
                options={[
                  { value: 'loose', label: 'Loose (includes features)' },
                  { value: 'strict', label: 'Strict (main artist only)' },
                ]}
                value={config.nm_match_mode}
                onSelect={v => update('nm_match_mode', v as NewMusicConfig['nm_match_mode'])}
              />
            </div>

            {/* Ignore keywords */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Ignore keywords
              </label>
              <input
                type="text"
                value={config.nm_ignore_keywords}
                onChange={e => update('nm_ignore_keywords', e.target.value)}
                placeholder="live, christmas, remix"
                className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 placeholder:text-[#333] focus:outline-none focus:border-accent"
              />
              <p className="text-[#444] text-[11px] mt-1">Comma-separated. Releases containing these words are excluded.</p>
            </div>

            {/* Ignore artists */}
            <div>
              <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                Ignore artists
              </label>
              <input
                type="text"
                value={config.nm_ignore_artists}
                onChange={e => update('nm_ignore_artists', e.target.value)}
                placeholder="Artist Name, Another Artist"
                className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 placeholder:text-[#333] focus:outline-none focus:border-accent"
              />
              <p className="text-[#444] text-[11px] mt-1">Comma-separated artist names to skip.</p>
            </div>

            {/* Schedule */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-text-muted uppercase tracking-widest">Schedule</span>
                <Toggle
                  on={config.nm_schedule_enabled}
                  onChange={v => update('nm_schedule_enabled', v)}
                />
              </div>
              {config.nm_schedule_enabled && (
                <div className="flex gap-3">
                  <select
                    value={config.nm_schedule_weekday}
                    onChange={e => update('nm_schedule_weekday', parseInt(e.target.value))}
                    className="bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                  >
                    {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                  <select
                    value={config.nm_schedule_hour}
                    onChange={e => update('nm_schedule_hour', parseInt(e.target.value))}
                    className="bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                  >
                    {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                  </select>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // ---------------------------------------------------------------------------
  // Progress view
  // ---------------------------------------------------------------------------

  const progressView = (
    <div className="py-10 space-y-6">
      {/* Progress bar */}
      <div className="w-full bg-[#1a1a1a] h-1">
        <div
          className="h-1 bg-accent transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Stage list */}
      <div className="space-y-3">
        {STAGES.slice(0, -1).map((stage, i) => {
          const done = i < stageIdx;
          const active = i === stageIdx;
          return (
            <div key={stage.key} className={`flex items-center gap-3 text-sm transition-colors ${
              done ? 'text-text-muted' : active ? 'text-text-primary' : 'text-[#333]'
            }`}>
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                done ? 'bg-accent' : active ? 'bg-accent animate-pulse' : 'bg-[#2a2a2a]'
              }`} />
              {stage.label}
              {done && <span className="text-[#444] text-xs ml-auto">done</span>}
            </div>
          );
        })}
      </div>
    </div>
  );

  // ---------------------------------------------------------------------------
  // Release card
  // ---------------------------------------------------------------------------

  const ReleaseCard = ({ release }: { release: DiscoveredRelease }) => (
    <div className={`group flex flex-col gap-0 ${!release.in_library ? 'opacity-60' : ''}`}>
      <div className="relative overflow-hidden bg-[#111] aspect-square">
        {release.cover_url ? (
          <img
            src={release.cover_url}
            alt={release.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Music2 size={32} className="text-[#2a2a2a]" />
          </div>
        )}
        {release.in_library && (
          <span className="absolute top-2 right-2 bg-accent text-black text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5">
            In library
          </span>
        )}
      </div>
      <div className="pt-2 pb-1">
        <p className="text-text-primary text-sm font-medium leading-snug truncate">{release.title}</p>
        <p className="text-text-muted text-xs truncate mt-0.5">{release.artist_name}</p>
        <div className="flex items-center gap-2 mt-1">
          {release.release_date && (
            <span className="text-[10px] text-[#444]">{release.release_date.slice(0, 7)}</span>
          )}
          {release.record_type && (
            <span className="text-[10px] text-[#333] uppercase tracking-wide">{release.record_type}</span>
          )}
        </div>
      </div>
    </div>
  );

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const hasResults = results !== null && results.length > 0;
  const periodLabel = PERIODS.find(p => p.value === config.nm_period)?.label ?? config.nm_period;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">New Music</h2>
          <p className="text-text-muted text-sm mt-0.5">
            {hasResults && runSummary
              ? `${runSummary.releases_found} releases · ${periodLabel} window`
              : 'Recent releases from your artist neighborhood.'}
          </p>
        </div>
        {hasResults && !running && (
          <button
            onClick={() => setEditOpen(v => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-text-muted border border-[#1a1a1a] hover:border-[#2a2a2a] hover:text-text-primary transition-colors"
          >
            <Settings size={12} />
            {editOpen ? 'Close' : 'Edit'}
          </button>
        )}
      </div>

      {/* Running state */}
      {running && progressView}

      {/* Config — shown on first visit OR when edit panel is open */}
      {!running && (!hasResults || editOpen) && (
        <div className="bg-[#0a0a0a] border border-[#1a1a1a] p-5 space-y-6">
          {configFields}

          <div className="flex items-center gap-3 pt-2 border-t border-[#181818]">
            <button
              onClick={handleRun}
              disabled={running || !configLoaded}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold bg-accent text-black hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Zap size={14} />
              Run
            </button>
            <p className="text-[#333] text-xs">Settings save automatically on run.</p>
          </div>
        </div>
      )}

      {/* Run button when results exist and edit is closed */}
      {!running && hasResults && !editOpen && (
        <button
          onClick={handleRun}
          disabled={running || !configLoaded}
          className="flex items-center gap-2 px-4 py-1.5 text-sm font-semibold border border-[#2a2a2a] text-text-muted hover:border-accent hover:text-accent transition-colors disabled:opacity-40"
        >
          <Zap size={13} />
          Run again
        </button>
      )}

      {/* Results grid */}
      {!running && hasResults && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
          {results!.map(r => <ReleaseCard key={r.id} release={r} />)}
        </div>
      )}

      {/* No results after run */}
      {!running && results !== null && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 gap-3 border border-dashed border-[#1a1a1a]">
          <p className="text-text-muted text-sm">No releases found</p>
          <p className="text-[#444] text-xs text-center max-w-xs">
            Try expanding the release window, lowering minimum listens, or choosing a longer listening period.
          </p>
        </div>
      )}
    </div>
  );
}
