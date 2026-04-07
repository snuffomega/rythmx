import { useState, useEffect, useMemo } from 'react';
import { Zap, ChevronDown, ChevronUp, Music2, Trash2, X, ExternalLink, Loader2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { forgeBuildsApi, forgeNewMusicApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import { useForgePipelineStore } from '../stores/useForgePipelineStore';
import { Toggle } from '../components/common';
import { FormInput } from '../components/forms';
import { getForgeReleaseTarget, openExternalReleaseUrl } from '../utils/forgeReleaseLinks';
import type {
  NewMusicConfig,
  DiscoveredRelease,
  ReleasePreviewTrack,
  ReleasePreviewSource,
} from '../types';

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
  { key: 'persist', label: 'Saving discovered releases' },
  { key: 'done', label: 'Done' },
];

type NewMusicSortKey =
  | 'release_desc'
  | 'artist_az'
  | 'artist_za'
  | 'album_first'
  | 'single_first';

function compareDateDesc(a: string | null | undefined, b: string | null | undefined): number {
  const av = String(a || '');
  const bv = String(b || '');
  if (av === bv) return 0;
  return av > bv ? -1 : 1;
}

function compareStringAsc(a: string | null | undefined, b: string | null | undefined): number {
  return String(a || '').localeCompare(String(b || ''), undefined, { sensitivity: 'base' });
}

function releaseTypeRank(recordType: string | null | undefined, singleFirst: boolean): number {
  const rt = String(recordType || '').toLowerCase();
  if (singleFirst) {
    if (rt === 'single' || rt === 'ep') return 0;
    if (rt === 'album' || rt === 'compile') return 1;
    return 2;
  }
  if (rt === 'album' || rt === 'compile') return 0;
  if (rt === 'ep') return 1;
  if (rt === 'single') return 2;
  return 3;
}

function compareNewMusicRelease(
  a: DiscoveredRelease,
  b: DiscoveredRelease,
  sortKey: NewMusicSortKey
): number {
  if (sortKey === 'artist_az') {
    const byArtist = compareStringAsc(a.artist_name, b.artist_name);
    return byArtist !== 0 ? byArtist : compareDateDesc(a.release_date, b.release_date);
  }
  if (sortKey === 'artist_za') {
    const byArtist = compareStringAsc(b.artist_name, a.artist_name);
    return byArtist !== 0 ? byArtist : compareDateDesc(a.release_date, b.release_date);
  }
  if (sortKey === 'album_first' || sortKey === 'single_first') {
    const typeDiff =
      releaseTypeRank(a.record_type, sortKey === 'single_first')
      - releaseTypeRank(b.record_type, sortKey === 'single_first');
    if (typeDiff !== 0) return typeDiff;
    const byDate = compareDateDesc(a.release_date, b.release_date);
    if (byDate !== 0) return byDate;
    return compareStringAsc(a.artist_name, b.artist_name);
  }
  const byDate = compareDateDesc(a.release_date, b.release_date);
  if (byDate !== 0) return byDate;
  return compareStringAsc(a.artist_name, b.artist_name);
}

function formatDuration(ms: number | null | undefined): string {
  const totalSeconds = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ForgeNewMusic() {
  const navigate = useNavigate();
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);
  const toastInfo = useToastStore(s => s.info);

  const [config, setConfig] = useState<NewMusicConfig>(DEFAULT_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [running, setRunning] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [stageIdx, setStageIdx] = useState(0);
  const [progress, setProgress] = useState(0);
  const pipelineState = useForgePipelineStore(s => s.pipelines.new_music);
  const resetPipeline = useForgePipelineStore(s => s.resetPipeline);

  const [results, setResults] = useState<DiscoveredRelease[] | null>(null);
  const [resultSort, setResultSort] = useState<NewMusicSortKey>('release_desc');
  const [runSummary, setRunSummary] = useState<{ releases_found: number } | null>(null);
  const [filteredReleases, setFilteredReleases] = useState<DiscoveredRelease[]>([]);
  const [filteredOpen, setFilteredOpen] = useState(false);
  const [previewRelease, setPreviewRelease] = useState<DiscoveredRelease | null>(null);
  const [previewTracks, setPreviewTracks] = useState<ReleasePreviewTrack[]>([]);
  const [previewSources, setPreviewSources] = useState<ReleasePreviewSource[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

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

  useEffect(() => {
    if (!running) return;
    const stageKey = pipelineState.stage || 'history';
    const idx = Math.max(0, STAGES.findIndex(s => s.key === stageKey));
    setStageIdx(idx);

    if (stageKey === 'done') {
      setProgress(100);
      return;
    }
    const total = Math.max(1, pipelineState.total || 1);
    const processed = Math.max(0, Math.min(total, pipelineState.processed || 0));
    const stageProgress = (processed / total) * (100 / Math.max(STAGES.length - 1, 1));
    const base = (idx / Math.max(STAGES.length - 1, 1)) * 100;
    setProgress(Math.min(99, Math.round(base + stageProgress)));
  }, [running, pipelineState.stage, pipelineState.processed, pipelineState.total]);

  // ---------------------------------------------------------------------------
  // Run handler
  // ---------------------------------------------------------------------------

  const handleRun = async () => {
    resetPipeline('new_music');
    setRunning(true);
    setStageIdx(0);
    setProgress(0);
    setResults(null);
    toastInfo('Forge in progress: New Music run started');

    await forgeNewMusicApi.saveConfig(config).catch(() => {});

    try {
      const data = await forgeNewMusicApi.run(config);
      setStageIdx(STAGES.length - 1);
      setProgress(100);
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
          ? `${data.releases_found} releases found. Build queued in Builder.`
          : `${data.releases_found} releases found`
      );
    } catch {
      toastError('Pipeline failed - check logs');
    } finally {
      setRunning(false);
    }
  };

  const handleSaveSchedule = async () => {
    setSavingSchedule(true);
    try {
      await forgeNewMusicApi.saveConfig({
        nm_schedule_enabled: config.nm_schedule_enabled,
        nm_schedule_weekday: config.nm_schedule_weekday,
        nm_schedule_hour: config.nm_schedule_hour,
      });
      toastSuccess('New Music schedule saved');
    } catch {
      toastError('Failed to save New Music schedule');
    } finally {
      setSavingSchedule(false);
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
              : 'text-text-muted border-border hover:border-border-strong hover:text-text-muted'
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
  const sortedResults = useMemo(
    () => (results ?? []).slice().sort((a, b) => compareNewMusicRelease(a, b, resultSort)),
    [results, resultSort]
  );
  const sortedFilteredReleases = useMemo(
    () => filteredReleases.slice().sort((a, b) => compareNewMusicRelease(a, b, resultSort)),
    [filteredReleases, resultSort]
  );

  const closePreview = () => {
    setPreviewRelease(null);
    setPreviewTracks([]);
    setPreviewSources([]);
    setPreviewError(null);
    setPreviewLoading(false);
  };

  const openPreview = async (release: DiscoveredRelease) => {
    setPreviewRelease(release);
    setPreviewTracks([]);
    setPreviewError(null);
    setPreviewLoading(true);

    const fallbackTarget = getForgeReleaseTarget(release);
    const fallbackSources: ReleasePreviewSource[] =
      fallbackTarget?.kind === 'external'
        ? [{ provider: 'deezer', url: fallbackTarget.url }]
        : [];
    setPreviewSources(fallbackSources);

    try {
      const data = await forgeNewMusicApi.getReleaseTracks(release.id);
      setPreviewTracks(data.tracks || []);
      setPreviewSources((data.sources && data.sources.length > 0) ? data.sources : fallbackSources);
    } catch {
      setPreviewError('Could not load track list for this release.');
    } finally {
      setPreviewLoading(false);
    }
  };

  const openLibraryArtist = (release: DiscoveredRelease) => {
    const target = getForgeReleaseTarget(release);
    if (target?.kind !== 'library-artist') return;
    closePreview();
    navigate({ to: '/library/artist/$id', params: { id: target.artistId } });
  };

  const openProviderLink = (source: ReleasePreviewSource) => {
    openExternalReleaseUrl(source.url);
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

      {/* Config panel - always visible */}
      <div className="bg-base border border-border-subtle p-5 space-y-6">

        {/* Listening period - custom dot slider */}
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
            <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-px bg-border-input" />
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
                      : 'bg-surface border-border-strong group-hover:border-border-strong'
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
                className={`text-[10px] transition-colors ${i === periodIdx ? 'text-accent' : 'text-text-faint'}`}
              >
                {label}
              </span>
            ))}
          </div>
          <p className="text-text-dim text-[11px] mt-2">How far back in your listening history to look for seed artists.</p>
        </div>

        {/* Release window + Min listens - same row, left-aligned */}
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
            <p className="text-text-dim text-[11px] mt-1.5">How far back to look for new releases.</p>
          </div>
          <div className="flex-shrink-0">
            <FormInput
              label="Min listens"
              type="number"
              min={1}
              max={500}
              value={config.nm_min_scrobbles}
              onChange={e => update('nm_min_scrobbles', parseInt(e.target.value) || 1)}
              className="w-20"
              helperText="Min plays to qualify."
            />
          </div>
        </div>

        {/* Release types + Match mode - same row, left-aligned */}
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

        {/* Advanced - ignore criteria + schedule only */}
        <div>
          <button
            onClick={() => setAdvancedOpen(v => !v)}
            className="flex items-center gap-1.5 text-text-dim hover:text-text-muted text-xs font-semibold uppercase tracking-widest transition-colors"
          >
            {advancedOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            Advanced
          </button>

          {advancedOpen && (
            <div className="mt-5 space-y-5 border-l border-border-subtle pl-4">
              <FormInput
                label="Ignore keywords"
                type="text"
                value={config.nm_ignore_keywords}
                onChange={e => update('nm_ignore_keywords', e.target.value)}
                placeholder="live, christmas, remix"
                className="w-full"
                helperText="Comma-separated. Releases containing these words are excluded."
              />

              <FormInput
                label="Ignore artists"
                type="text"
                value={config.nm_ignore_artists}
                onChange={e => update('nm_ignore_artists', e.target.value)}
                placeholder="Artist Name, Another Artist"
                className="w-full"
                helperText="Comma-separated artist names to skip."
              />

              {/* Clear results */}
              <div>
                <label className="block text-xs font-semibold text-text-muted uppercase tracking-widest mb-2">
                  Results
                </label>
                <button
                  onClick={handleClear}
                  disabled={running || !results?.length}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-text-muted border border-border hover:border-danger hover:text-danger transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <Trash2 size={11} />
                  Clear results
                </button>
                <p className="text-text-dim text-[11px] mt-1">Removes stored results from the database.</p>
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
                  <div className="flex gap-3 flex-wrap">
                    <select
                      value={config.nm_schedule_weekday}
                      onChange={e => update('nm_schedule_weekday', parseInt(e.target.value))}
                      className="bg-surface border border-border-input text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                    >
                      {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                    </select>
                    <select
                      value={config.nm_schedule_hour}
                      onChange={e => update('nm_schedule_hour', parseInt(e.target.value))}
                      className="bg-surface border border-border-input text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                    >
                      {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                    </select>
                    <button
                      onClick={handleSaveSchedule}
                      disabled={running || savingSchedule || !configLoaded}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-text-soft border border-border-input hover:border-accent hover:text-text-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {savingSchedule ? <Loader2 size={11} className="animate-spin" /> : null}
                      Set Schedule
                    </button>
                  </div>
                )}
                {!config.nm_schedule_enabled && (
                  <button
                    onClick={handleSaveSchedule}
                    disabled={running || savingSchedule || !configLoaded}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-text-soft border border-border-input hover:border-accent hover:text-text-primary transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {savingSchedule ? <Loader2 size={11} className="animate-spin" /> : null}
                    Set Schedule
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Run button + inline activity feed */}
        <div className="space-y-4 pt-2 border-t border-border-subtle">
          <div className="flex items-center gap-3">
            <button
              onClick={handleRun}
              disabled={running || !configLoaded}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold bg-accent text-black hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Zap size={14} />
              {running ? 'Running...' : 'Run'}
            </button>
            {!running && (
              <p className="text-text-faint text-xs">Run saves full config; Set Schedule saves schedule immediately.</p>
            )}
          </div>

          {/* Inline progress feed */}
          {running && (
            <div className="space-y-3">
              <div className="w-full bg-surface-raised h-px">
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
                        done ? 'text-text-muted' : active ? 'text-text-primary' : 'text-text-faint'
                      }`}
                    >
                      <span className={`w-1 h-1 rounded-full flex-shrink-0 ${
                        done ? 'bg-accent' : active ? 'bg-accent animate-pulse' : 'bg-border-input'
                      }`} />
                      {stage.label}
                      {done && <span className="text-text-dim text-[10px] ml-auto">done</span>}
                    </div>
                  );
                })}
              </div>
              {pipelineState.message && (
                <p className="text-text-muted text-xs">{pipelineState.message}</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Results grid */}
      {!running && hasResults && (
        <>
          <div className="flex items-center justify-between gap-3">
            <p className="text-text-muted text-xs uppercase tracking-widest">Sort Results</p>
            <select
              value={resultSort}
              onChange={e => setResultSort(e.target.value as NewMusicSortKey)}
              className="bg-surface border border-border-input text-text-primary text-xs px-2.5 py-1.5 focus:outline-none focus:border-accent"
            >
              <option value="release_desc">Release date (newest)</option>
              <option value="artist_az">Artist A-Z</option>
              <option value="artist_za">Artist Z-A</option>
              <option value="album_first">Type: album first</option>
              <option value="single_first">Type: single first</option>
            </select>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
            {sortedResults.map(r => <ReleaseCard key={r.id} release={r} onOpen={() => { void openPreview(r); }} />)}
          </div>
        </>
      )}

      {/* Filtered releases accordion */}
      {!running && sortedFilteredReleases.length > 0 && (
        <div className="border border-border-subtle">
          <button
            onClick={() => setFilteredOpen(v => !v)}
            className="w-full flex items-center justify-between px-4 py-3 text-xs font-semibold text-text-dim hover:text-text-muted uppercase tracking-widest transition-colors"
          >
            <span>
              Filtered out - {sortedFilteredReleases.length} release{sortedFilteredReleases.length !== 1 ? 's' : ''}
            </span>
            {filteredOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
          {filteredOpen && (
            <div className="border-t border-border-subtle divide-y divide-surface">
              {sortedFilteredReleases.map(r => (
                <div key={r.id} className="flex items-center gap-3 px-4 py-2.5 opacity-40">
                  {r.cover_url ? (
                    <img src={r.cover_url} alt={r.title} className="w-8 h-8 object-cover flex-shrink-0" />
                  ) : (
                    <div className="w-8 h-8 bg-surface flex items-center justify-center flex-shrink-0">
                      <Music2 size={12} className="text-text-faint" />
                    </div>
                  )}
                  <div className="min-w-0">
                    <p className="text-text-primary text-xs font-medium truncate">{r.title}</p>
                    <p className="text-text-muted text-[11px] truncate">{r.artist_name}</p>
                  </div>
                  {r.release_date && (
                    <span className="text-[10px] text-text-faint ml-auto flex-shrink-0">{r.release_date.slice(0, 7)}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* No results after run */}
      {!running && results !== null && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 gap-3 border border-dashed border-border-subtle">
          <p className="text-text-muted text-sm">No releases found</p>
          <p className="text-text-dim text-xs text-center max-w-xs">
            Try expanding the release window, lowering minimum listens, or choosing a longer listening period.
          </p>
        </div>
      )}

      {previewRelease && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="w-full max-w-2xl bg-surface-sunken border border-border-subtle max-h-[85vh] overflow-hidden flex flex-col">
            <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border-subtle">
              <div className="min-w-0">
                <p className="text-text-primary text-base font-semibold truncate">{previewRelease.title}</p>
                <p className="text-text-muted text-sm truncate">{previewRelease.artist_name}</p>
              </div>
              <button
                onClick={closePreview}
                className="text-text-muted hover:text-text-primary transition-colors"
                aria-label="Close release preview"
              >
                <X size={16} />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4 overflow-y-auto">
              <div className="flex flex-wrap gap-2">
                {previewSources.map(source => (
                  <button
                    key={`${source.provider}-${source.url}`}
                    onClick={() => openProviderLink(source)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-widest border border-border-input text-text-soft hover:border-accent hover:text-accent transition-colors"
                  >
                    {source.provider}
                    <ExternalLink size={11} />
                  </button>
                ))}
                {previewRelease.library_artist_id && (
                  <button
                    onClick={() => openLibraryArtist(previewRelease)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-widest border border-border-input text-text-soft hover:border-accent hover:text-accent transition-colors"
                  >
                    library artist
                  </button>
                )}
              </div>

              {previewLoading && (
                <p className="text-text-muted text-sm">Loading tracks...</p>
              )}

              {!previewLoading && previewError && (
                <p className="text-danger text-sm">{previewError}</p>
              )}

              {!previewLoading && !previewError && previewTracks.length === 0 && (
                <p className="text-text-muted text-sm">No track listing available for this release.</p>
              )}

              {!previewLoading && !previewError && previewTracks.length > 0 && (
                <div className="border border-border-subtle divide-y divide-surface">
                  {previewTracks.map((track, idx) => (
                    <div key={`${track.title}-${idx}`} className="flex items-center gap-3 px-3 py-2.5">
                      <span className="text-text-muted text-xs tabular-nums w-8 text-right">
                        {track.track_number || idx + 1}
                      </span>
                      <p className="text-text-primary text-sm truncate flex-1">{track.title}</p>
                      <span className="text-text-muted text-xs tabular-nums">
                        {formatDuration(track.duration_ms)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
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
      <div className="relative overflow-hidden bg-surface-highlight aspect-square border border-border-subtle group-hover:border-border-strong transition-colors">
        {release.cover_url ? (
          <img
            src={release.cover_url}
            alt={release.title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Music2 size={32} className="text-text-faint" />
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
            <span className="text-[10px] text-text-dim">{release.release_date.slice(0, 7)}</span>
          )}
          {release.record_type && (
            <span className="text-[10px] text-text-faint uppercase tracking-wide">{release.record_type}</span>
          )}
        </div>
      </div>
    </div>
  );
}
