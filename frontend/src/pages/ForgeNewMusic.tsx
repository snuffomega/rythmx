import { useState, useEffect, useRef } from 'react';
import { Zap, ChevronDown, ChevronUp, Music2, Trash2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { forgeBuildsApi, forgeNewMusicApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import { Toggle } from '../components/common';
import { getForgeReleaseTarget, openExternalReleaseUrl } from '../utils/forgeReleaseLinks';
import type { NewMusicConfig, DiscoveredRelease } from '../types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PERIOD_VALUES = ['7day', '1month', '3month', '6month', '12month', 'overall'] as const;
const PERIOD_LABELS = ['7 days', '1 month', '3 months', '6 months', '12 months', 'All time'];

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

const STAGES = [
  { key: 'history', label: 'Reading your listening history' },
  { key: 'releases', label: 'Finding releases from your top artists' },
  { key: 'done', label: 'Done' },
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

  const [running, setRunning] = useState(false);
  const [stageIdx, setStageIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const progressTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [results, setResults] = useState<DiscoveredRelease[] | null>(null);
  const [runSummary, setRunSummary] = useState<{ releases_found: number } | null>(null);
  const [filteredReleases, setFilteredReleases] = useState<DiscoveredRelease[]>([]);
  const [filteredOpen, setFilteredOpen] = useState(false);

  const update = <K extends keyof NewMusicConfig>(key: K, value: NewMusicConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }));

  const periodIdx = Math.max(0, PERIOD_VALUES.indexOf(config.nm_period as typeof PERIOD_VALUES[number]));

  const handleClear = async () => {
    try {
      await forgeNewMusicApi.clear();
      setResults(null);
      setRunSummary(null);
      setFilteredReleases([]);
      toastSuccess('Results cleared');
    } catch {
      toastError('Clear failed');
    }
  };

  // Load config + existing results on mount
  useEffect(() => {
    forgeNewMusicApi.getConfig()
      .then(cfg => { setConfig(cfg); setConfigLoaded(true); })
      .catch(() => { setConfigLoaded(true); });

    forgeNewMusicApi.getResults()
      .then(releases => { if (releases.length > 0) setResults(releases); })
      .catch(() => {});
  }, []);

  useEffect(() => () => {
    if (progressTimerRef.current) clearInterval(progressTimerRef.current);
  }, []);

  // ---------------------------------------------------------------------------
  // Progress animation
  // ---------------------------------------------------------------------------

  const STAGE_PCTS = [10, 90, 100];

  const startProgress = () => {
    setStageIdx(0);
    setProgress(0);
    let currentPct = 0;
    let currentStage = 0;

    progressTimerRef.current = setInterval(() => {
      const targetPct = STAGE_PCTS[currentStage] ?? 90;
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
    startProgress();

    await forgeNewMusicApi.saveConfig(config).catch(() => {});

    try {
      const data = await forgeNewMusicApi.run(config);
      finishProgress();
      setResults(data.releases);
      setRunSummary({ releases_found: data.releases_found });
      setFilteredReleases(data.filtered_releases ?? []);
      setFilteredOpen(false);

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
  // Render
  // ---------------------------------------------------------------------------

  const hasResults = results !== null && results.length > 0;

  const openRelease = (release: DiscoveredRelease) => {
    const target = getForgeReleaseTarget(release);
    if (!target) return;
    if (target.kind === 'library-artist') {
      navigate({ to: '/library/artist/$id', params: { id: target.artistId } });
      return;
    }
    openExternalReleaseUrl(target.url);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-lg font-semibold text-text-primary">New Music</h2>
        <p className="text-text-muted text-sm mt-0.5">
          {hasResults && runSummary
            ? `${runSummary.releases_found} releases found`
            : 'Recent releases from artists you listen to.'}
        </p>
      </div>

      {/* Config panel — always visible */}
      <div className="bg-[#0a0a0a] border border-[#1a1a1a] p-5 space-y-6">

        {/* Listening period — custom dot slider */}
        <div>
          <div className="flex items-end justify-between mb-4">
            <label className="text-xs font-semibold text-text-muted uppercase tracking-widest">
              Listening period
            </label>
            <span className="text-2xl font-bold text-text-primary leading-none">
              {PERIOD_LABELS[periodIdx]}
            </span>
          </div>

          {/* Track + dots */}
          <div className="relative flex items-center h-6">
            {/* Background track */}
            <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-px bg-[#2a2a2a]" />
            {/* Filled track */}
            <div
              className="absolute left-0 top-1/2 -translate-y-1/2 h-px transition-all duration-200"
              style={{
                width: `${(periodIdx / 5) * 100}%`,
                backgroundColor: '#D4F53C',
              }}
            />
            {/* Dots */}
            <div className="relative flex justify-between w-full">
              {PERIOD_LABELS.map((label, i) => (
                <button
                  key={i}
                  onClick={() => update('nm_period', PERIOD_VALUES[i] as NewMusicConfig['nm_period'])}
                  className="flex flex-col items-center gap-2 group"
                  title={label}
                >
                  <div className={`w-2.5 h-2.5 rounded-full border transition-all duration-150 ${
                    i <= periodIdx
                      ? 'border-transparent'
                      : 'bg-[#111] border-[#333] group-hover:border-[#555]'
                  }`}
                    style={i <= periodIdx ? { backgroundColor: '#D4F53C' } : undefined}
                  />
                </button>
              ))}
            </div>
          </div>

          {/* Period labels below dots */}
          <div className="flex justify-between mt-1.5">
            {PERIOD_LABELS.map((label, i) => (
              <span
                key={i}
                className={`text-[10px] transition-colors ${i === periodIdx ? 'text-accent' : 'text-[#333]'}`}
              >
                {label}
              </span>
            ))}
          </div>
          <p className="text-[#444] text-[11px] mt-2">How far back in your listening history to look for seed artists.</p>
        </div>

        {/* Release window + Min listens — same row, left-aligned */}
        <div className="flex items-start gap-8">
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
          <div className="flex-shrink-0">
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
            <p className="text-[#444] text-[11px] mt-1">Min plays to qualify.</p>
          </div>
        </div>

        {/* Release types + Match mode — same row, left-aligned */}
        <div className="flex items-start gap-8 flex-wrap">
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
          </div>
          <div>
            <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
              Match mode
            </label>
            <ChipRow
              options={[
                { value: 'loose', label: 'Loose' },
                { value: 'strict', label: 'Strict' },
              ]}
              value={config.nm_match_mode}
              onSelect={v => update('nm_match_mode', v as NewMusicConfig['nm_match_mode'])}
            />
          </div>
        </div>

        {/* Advanced — ignore criteria + schedule only */}
        <div>
          <button
            onClick={() => setAdvancedOpen(v => !v)}
            className="flex items-center gap-1.5 text-[#444] hover:text-[#666] text-xs font-semibold uppercase tracking-widest transition-colors"
          >
            {advancedOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            Advanced
          </button>

          {advancedOpen && (
            <div className="mt-5 space-y-5 border-l border-[#1a1a1a] pl-4">
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

              {/* Clear results */}
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                  Results
                </label>
                <button
                  onClick={handleClear}
                  disabled={running || !results?.length}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-[#555] border border-[#222] hover:border-danger hover:text-danger transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <Trash2 size={11} />
                  Clear results
                </button>
                <p className="text-[#444] text-[11px] mt-1">Removes stored results from the database.</p>
              </div>

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

        {/* Run button + inline activity feed */}
        <div className="space-y-4 pt-2 border-t border-[#181818]">
          <div className="flex items-center gap-3">
            <button
              onClick={handleRun}
              disabled={running || !configLoaded}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold bg-accent text-black hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Zap size={14} />
              {running ? 'Running…' : 'Run'}
            </button>
            {!running && (
              <p className="text-[#333] text-xs">Settings save automatically on run.</p>
            )}
          </div>

          {/* Inline progress feed */}
          {running && (
            <div className="space-y-3">
              <div className="w-full bg-[#1a1a1a] h-px">
                <div
                  className="h-px bg-accent transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="space-y-2">
                {STAGES.slice(0, -1).map((stage, i) => {
                  const done = i < stageIdx;
                  const active = i === stageIdx;
                  return (
                    <div
                      key={stage.key}
                      className={`flex items-center gap-2.5 text-xs transition-colors ${
                        done ? 'text-text-muted' : active ? 'text-text-primary' : 'text-[#2a2a2a]'
                      }`}
                    >
                      <span className={`w-1 h-1 rounded-full flex-shrink-0 ${
                        done ? 'bg-accent' : active ? 'bg-accent animate-pulse' : 'bg-[#2a2a2a]'
                      }`} />
                      {stage.label}
                      {done && <span className="text-[#444] text-[10px] ml-auto">done</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Results grid */}
      {!running && hasResults && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
          {results!.map(r => <ReleaseCard key={r.id} release={r} onOpen={() => openRelease(r)} />)}
        </div>
      )}

      {/* Filtered releases accordion */}
      {!running && filteredReleases.length > 0 && (
        <div className="border border-[#1a1a1a]">
          <button
            onClick={() => setFilteredOpen(v => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-[#444] hover:text-[#666] uppercase tracking-widest transition-colors"
          >
            <span>Filtered out — {filteredReleases.length} release{filteredReleases.length !== 1 ? 's' : ''}</span>
            {filteredOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
          {filteredOpen && (
            <div className="border-t border-[#1a1a1a] divide-y divide-[#111]">
              {filteredReleases.map(r => (
                <div key={r.id} className="flex items-center gap-3 px-4 py-2.5 opacity-40">
                  {r.cover_url ? (
                    <img src={r.cover_url} alt={r.title} className="w-8 h-8 object-cover flex-shrink-0" />
                  ) : (
                    <div className="w-8 h-8 bg-[#111] flex items-center justify-center flex-shrink-0">
                      <Music2 size={12} className="text-[#2a2a2a]" />
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="text-text-primary text-xs font-medium truncate">{r.title}</p>
                    <p className="text-text-muted text-[11px] truncate">{r.artist_name}</p>
                  </div>
                  {r.release_date && (
                    <span className="text-[10px] text-[#333] ml-auto flex-shrink-0">{r.release_date.slice(0, 7)}</span>
                  )}
                </div>
              ))}
            </div>
          )}
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

// ---------------------------------------------------------------------------
// Release card
// ---------------------------------------------------------------------------

function ReleaseCard({
  release,
  onOpen,
}: {
  release: DiscoveredRelease;
  onOpen?: () => void;
}) {
  const interactive = Boolean(onOpen);

  return (
    <div
      className={`group flex flex-col gap-0 ${interactive ? 'cursor-pointer' : ''}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (!onOpen) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <div className="relative overflow-hidden bg-[#181818] aspect-square border border-[#1e1e1e] group-hover:border-[#3a3a3a] transition-colors">
        {release.cover_url ? (
          <img
            src={release.cover_url}
            alt={release.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Music2 size={32} className="text-[#333]" />
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
}
