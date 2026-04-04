import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, Loader2, RefreshCw, Database, Radio, ChevronDown, ChevronUp, Key, Eye, EyeOff, Copy, Play, Square, Clock, Zap } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { useApi } from '../hooks/useApi';
import { settingsApi, libraryApi, libraryBrowseApi, setApiKey, enrichmentApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useEnrichmentStore } from '../stores/useEnrichmentStore';
import { useSettingsStore } from '../stores/useSettingsStore';
import type { LibraryPlatform, EnrichmentWorkerStatus, Settings, AuditItem, AuditCandidateItem } from '../types';

const PLATFORM_LABELS: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
};

interface ServiceRowProps {
  name: string;
  subtitle?: string;
  icon: React.ReactNode;
  configured?: boolean;
  onTest: () => Promise<{ connected: boolean; message?: string }>;
  extra?: React.ReactNode;
}

function ServiceCard({ name, subtitle, icon, configured, onTest, extra }: ServiceRowProps) {
  const [status, setStatus] = useState<'idle' | 'testing' | 'connected' | 'error'>('idle');

  const handleTest = async () => {
    setStatus('testing');
    try {
      const result = await onTest();
      setStatus(result.connected ? 'connected' : 'error');
    } catch {
      setStatus('error');
    }
  };

  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex items-stretch gap-3 min-h-[68px]">
      {/* LEFT: icon + name + optional dropdown */}
      <div className="flex-1 flex flex-col gap-2.5 min-w-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
            {icon}
          </div>
          {extra ?? (
            <div className="min-w-0">
              <p className="text-text-primary text-sm font-medium">{name}</p>
              {subtitle && <p className="text-[#444] text-[10px]">{subtitle}</p>}
            </div>
          )}
        </div>
      </div>

      {/* RIGHT: configured dot + test button */}
      <div className="flex flex-col items-end justify-between shrink-0">
        <span
          className={`w-2 h-2 rounded-full ${configured ? 'bg-accent' : 'bg-[#1e1e1e]'}`}
          title={configured ? 'Configured' : 'Not configured'}
        />
        <button
          onClick={handleTest}
          disabled={status === 'testing'}
          className="btn-ghost flex items-center gap-1.5 text-xs"
        >
          {status === 'testing' ? (
            <Loader2 size={12} className="animate-spin" />
          ) : status === 'connected' ? (
            <CheckCircle size={12} className="text-success" />
          ) : status === 'error' ? (
            <XCircle size={12} className="text-danger" />
          ) : (
            <RefreshCw size={12} />
          )}
          Test
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PipelineOrchestrator — hybrid layout (default: results summary, expanded: phase-grouped DAG)
// ---------------------------------------------------------------------------

// Pipeline phases matching backend DAG — grouped by service, not task.
// Each worker entry lists one or more backend keys to sum (handles the
// id_itunes_deezer combined "library" key during live runs vs individual
// sub-source keys from REST/completion payloads).
const PIPELINE_PHASES = [
  { id: 'sync', label: 'Library Sync', backendPhases: ['sync'], workers: [] },
  { id: 'identity', label: 'Identity Resolution', backendPhases: ['id_itunes_deezer', 'id_parallel'], workers: [
    { key: 'itunes_ids',   keys: ['itunes_artist', 'itunes'],  label: 'iTunes',   desc: 'Artist & album identity matching' },
    { key: 'deezer_ids',   keys: ['deezer_artist', 'deezer'],  label: 'Deezer',   desc: 'Artist & album identity matching' },
    { key: 'library',      keys: ['library'],                   label: 'iTunes / Deezer',  desc: 'Combined live progress' },
    { key: 'spotify_id',   keys: ['spotify_id'],                label: 'Spotify',  desc: 'Artist ID from Spotify' },
    { key: 'lastfm_id',    keys: ['lastfm_id'],                 label: 'Last.fm',  desc: 'Artist MBID from Last.fm' },
    { key: 'artist_art',   keys: ['artist_art'],                label: 'Artist Artwork',  desc: 'Fanart.tv + Deezer photos' },
  ]},
  { id: 'post', label: 'Post-Processing', backendPhases: ['ownership_sync', 'normalize_titles', 'missing_counts', 'canonical'], workers: [] },
  { id: 'rich', label: 'Rich Metadata', backendPhases: ['rich_data'], workers: [
    { key: 'itunes_rich',    keys: ['itunes_rich'],    label: 'iTunes Enrichment',   desc: 'Release date, genre, label' },
    { key: 'deezer_rich',    keys: ['deezer_rich'],    label: 'Deezer Enrichment',   desc: 'Record type, album art' },
    { key: 'spotify_genres', keys: ['spotify_genres'], label: 'Spotify Enrichment',  desc: 'Artist genre tags' },
    { key: 'lastfm_tags',   keys: ['lastfm_tags'],    label: 'Last.fm Enrichment',  desc: 'Community tags' },
    { key: 'lastfm_stats',  keys: ['lastfm_stats'],   label: 'Last.fm Stats',       desc: 'Playcount, listeners' },
  ]},
] as const;

// All backend keys across all phases (for overall totals — deduped).
const ALL_BACKEND_KEYS = [...new Set(
  PIPELINE_PHASES.flatMap(p => p.workers.flatMap(w => w.keys))
)];

// Human-readable label for the currently active worker (keyed by backend worker name).
const WORKER_LABELS: Record<string, string> = {
  library: 'iTunes/Deezer IDs',
  itunes_artist: 'iTunes', itunes: 'iTunes', deezer_artist: 'Deezer', deezer: 'Deezer',
  spotify_id: 'Spotify', lastfm_id: 'Last.fm', artist_art: 'Artist Artwork',
  itunes_rich: 'iTunes Enrichment', deezer_rich: 'Deezer Enrichment',
  spotify_genres: 'Spotify Enrichment', lastfm_tags: 'Last.fm Enrichment', lastfm_stats: 'Last.fm Stats',
};

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m.toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

/** Sum stats across one or more backend worker keys. */
function workerStats(workers: Record<string, EnrichmentWorkerStatus>, keys: readonly string[]) {
  let found = 0, notFound = 0, errors = 0, pending = 0;
  for (const k of keys) {
    const w = workers[k];
    if (!w) continue;
    found += w.found; notFound += w.not_found; errors += w.errors; pending += w.pending;
  }
  const total = found + notFound + errors + pending;
  const foundPct = total > 0 ? (found / total) * 100 : 0;
  const notFoundPct = total > 0 ? (notFound / total) * 100 : 0;
  const errorPct = total > 0 ? (errors / total) * 100 : 0;
  const processedPct = foundPct + notFoundPct + errorPct;
  return { found, notFound, errors, pending, total, foundPct, notFoundPct, errorPct, processedPct, hasData: total > 0 };
}

// Stacked progress bar used for both overall and per-phase/worker bars.
function ProgressBar({ foundPct, notFoundPct, errorPct, processedPct, isActive, height = 'h-1.5' }: {
  foundPct: number; notFoundPct: number; errorPct: number; processedPct: number; isActive: boolean; height?: string;
}) {
  return (
    <div className={`relative ${height} bg-surface-highlight rounded-full overflow-hidden`}>
      <div className="absolute top-0 left-0 h-full bg-accent transition-all duration-500 ease-out" style={{ width: `${foundPct}%` }} />
      <div className="absolute top-0 h-full bg-red-500/50 transition-all duration-500 ease-out" style={{ left: `${foundPct}%`, width: `${notFoundPct}%` }} />
      <div className="absolute top-0 h-full bg-red-500/70 transition-all duration-500 ease-out" style={{ left: `${foundPct + notFoundPct}%`, width: `${errorPct}%` }} />
      {isActive && processedPct < 100 && (
        <div className="absolute top-0 h-full overflow-hidden" style={{ left: `${processedPct}%`, width: `${100 - processedPct}%` }}>
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent animate-shimmer" />
        </div>
      )}
    </div>
  );
}

interface PipelineOrchestratorProps {
  running: boolean;
  workers: Record<string, EnrichmentWorkerStatus>;
  activeWorkers: Set<string>;
  elapsedMs: number;
  phase: string | null;
  libraryTrackCount?: number;
  libraryLastSynced?: string;
  platform?: LibraryPlatform;
  onRunFull: () => void;
  onStop: () => void;
}

function PipelineOrchestrator({ running, workers, activeWorkers, elapsedMs, phase, libraryTrackCount, libraryLastSynced, platform, onRunFull, onStop }: PipelineOrchestratorProps) {
  const [showStages, setShowStages] = useState(false);

  // Overall totals across all backend worker keys
  const totals = ALL_BACKEND_KEYS.reduce(
    (acc, key) => {
      const s = workerStats(workers, [key]);
      acc.found += s.found; acc.notFound += s.notFound; acc.errors += s.errors; acc.total += s.total;
      return acc;
    },
    { found: 0, notFound: 0, errors: 0, total: 0 }
  );
  const totalProcessed = totals.found + totals.notFound + totals.errors;
  const overallPct = totals.total > 0 ? (totalProcessed / totals.total) * 100 : 0;
  const enrichedPct = totals.total > 0 ? (totals.found / totals.total) * 100 : 0;
  const overallFoundPct = totals.total > 0 ? (totals.found / totals.total) * 100 : 0;
  const overallNotFoundPct = totals.total > 0 ? (totals.notFound / totals.total) * 100 : 0;
  const overallErrorPct = totals.total > 0 ? (totals.errors / totals.total) * 100 : 0;

  // "Currently: X" label — most recently active worker
  const activeLabel = (() => {
    const active = [...activeWorkers];
    if (active.length === 0) return null;
    if (active.length === 1) return WORKER_LABELS[active[0]] ?? active[0];
    return `${active.length} sources`;
  })();

  // Sources with data (for "across N sources" summary)
  const sourcesWithData = ALL_BACKEND_KEYS.filter(k => workerStats(workers, [k]).hasData).length;

  // Determine phase state for each pipeline phase
  const phaseIndex = phase ? PIPELINE_PHASES.findIndex(p => (p.backendPhases as readonly string[]).includes(phase)) : -1;

  return (
    <div data-testid="pipeline-orchestrator" className="max-w-3xl">
      {(running || elapsedMs > 0) && (
        <div className="flex items-center gap-2 text-sm font-mono text-text-muted mb-4">
          <Clock size={14} />
          <span>{formatElapsed(elapsedMs)}</span>
        </div>
      )}

      {/* Overall progress — always visible */}
      <div className="mb-4 p-4 bg-surface rounded-sm border border-[#1a1a1a]">
        {running && activeLabel ? (
          <div className="flex items-center gap-2 mb-2">
            <Zap size={12} className="text-accent animate-pulse" />
            <span className="text-xs font-mono text-text-secondary">Currently: {activeLabel}</span>
          </div>
        ) : totals.total > 0 && !running ? (
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle size={12} className="text-accent" />
            <span className="text-xs font-mono text-text-secondary">
              {totals.found.toLocaleString()} items enriched across {sourcesWithData} sources
            </span>
          </div>
        ) : null}

        <div className="flex items-center justify-between mb-1.5">
          <span className="text-[10px] font-mono text-text-muted uppercase tracking-wider">
            {totalProcessed.toLocaleString()} / {totals.total.toLocaleString()} items
          </span>
          <span className="text-[10px] font-mono text-text-muted">{overallPct.toFixed(1)}%</span>
        </div>

        <ProgressBar
          foundPct={overallFoundPct}
          notFoundPct={overallNotFoundPct}
          errorPct={overallErrorPct}
          processedPct={overallFoundPct + overallNotFoundPct + overallErrorPct}
          isActive={running}
        />

        <div className="flex items-center gap-4 mt-2 text-[10px] font-mono text-text-muted">
          <span className="text-accent">{totals.found.toLocaleString()} enriched</span>
          {totals.notFound > 0 && <span className="text-red-400/70">{totals.notFound.toLocaleString()} not found</span>}
          {totals.errors > 0 && <span className="text-red-400">{totals.errors.toLocaleString()} errors</span>}
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 mb-4">
        {!running ? (
          <button onClick={onRunFull} className="btn-primary flex items-center gap-2 text-sm">
            <Play size={14} />
            Start Enrichment
          </button>
        ) : (
          <button onClick={onStop} className="btn-danger flex items-center gap-2 text-sm">
            <Square size={14} />
            Stop
          </button>
        )}
        {totals.total > 0 && (
          <span className="text-xs font-mono text-accent font-medium">
            {enrichedPct.toFixed(0)}% enriched
          </span>
        )}
      </div>

      {/* Pipeline stages toggle */}
      <button
        onClick={() => setShowStages(v => !v)}
        className="flex items-center gap-1.5 text-xs font-mono text-text-muted hover:text-text-secondary transition-colors mb-3"
      >
        {showStages ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {showStages ? 'Hide pipeline stages' : 'Show pipeline stages'}
      </button>

      {/* Phase-grouped DAG view */}
      {showStages && (
        <div className="space-y-2 border-l border-[#1a1a1a] ml-1 pl-4">
          {PIPELINE_PHASES.map((phaseDef, idx) => {
            const isPhaseActive = running && phaseIndex === idx;
            // No-worker phases (sync, post) are done if pipeline has moved past them,
            // or at rest if any worker across the pipeline has data (meaning a run completed).
            const anyDataExists = totals.total > 0;
            const isPhaseDone = running
              ? phaseIndex > idx
              : phaseDef.workers.length > 0
                ? phaseDef.workers.some(w => workerStats(workers, w.keys).hasData)
                : anyDataExists;
            const isPhaseWaiting = running && phaseIndex < idx;
            const hasWorkers = phaseDef.workers.length > 0;

            // Phase-level aggregate stats
            const phaseStats = hasWorkers ? phaseDef.workers.reduce(
              (acc, w) => {
                const s = workerStats(workers, w.keys);
                acc.found += s.found; acc.notFound += s.notFound; acc.errors += s.errors;
                acc.total += s.total;
                return acc;
              },
              { found: 0, notFound: 0, errors: 0, total: 0 }
            ) : null;
            const phaseProcessed = phaseStats ? phaseStats.found + phaseStats.notFound + phaseStats.errors : 0;
            const phasePct = phaseStats && phaseStats.total > 0 ? (phaseProcessed / phaseStats.total) * 100 : 0;
            const phaseFoundPct = phaseStats && phaseStats.total > 0 ? (phaseStats.found / phaseStats.total) * 100 : 0;
            const phaseNotFoundPct = phaseStats && phaseStats.total > 0 ? (phaseStats.notFound / phaseStats.total) * 100 : 0;
            const phaseErrorPct = phaseStats && phaseStats.total > 0 ? (phaseStats.errors / phaseStats.total) * 100 : 0;

            return (
              <div
                key={phaseDef.id}
                className={`p-3 rounded-sm border transition-all duration-300 ${
                  isPhaseActive ? 'bg-surface border-accent/20'
                  : isPhaseDone ? 'bg-surface/50 border-[#1a1a1a]'
                  : 'bg-surface/30 border-[#141414]'
                }`}
              >
                {/* Phase header */}
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    {isPhaseActive && <Zap size={10} className="text-accent animate-pulse" />}
                    {isPhaseDone && !isPhaseActive && <CheckCircle size={10} className="text-accent/60" />}
                    {isPhaseWaiting && <span className="w-2.5 h-2.5 rounded-full border border-[#333] inline-block" />}
                    {!running && !isPhaseDone && <span className="w-2.5 h-2.5 rounded-full border border-[#333] inline-block" />}
                    <span className={`text-xs font-medium ${
                      isPhaseActive ? 'text-text-primary' : isPhaseDone ? 'text-text-secondary' : 'text-text-muted'
                    }`}>{phaseDef.label}</span>
                  </div>
                  {phaseStats && phaseStats.total > 0 && (
                    <span className="text-[10px] font-mono text-text-muted/60">
                      {phasePct.toFixed(0)}%
                    </span>
                  )}
                </div>

                {/* Phase bar (only for phases with workers) */}
                {hasWorkers && phaseStats && phaseStats.total > 0 && (
                  <>
                    <ProgressBar
                      foundPct={phaseFoundPct}
                      notFoundPct={phaseNotFoundPct}
                      errorPct={phaseErrorPct}
                      processedPct={phaseFoundPct + phaseNotFoundPct + phaseErrorPct}
                      isActive={isPhaseActive}
                      height="h-1"
                    />
                    <div className="flex items-center gap-3 mt-1 text-[10px] font-mono text-text-muted">
                      <span>{phaseProcessed.toLocaleString()} / {phaseStats.total.toLocaleString()}</span>
                      <span className="text-accent">{phaseStats.found.toLocaleString()} found</span>
                      {phaseStats.notFound > 0 && <span className="text-red-400/70">{phaseStats.notFound.toLocaleString()} miss</span>}
                    </div>
                  </>
                )}

                {/* No-bar phases: just show status text */}
                {!hasWorkers && (
                  <div className="mt-0.5">
                    {phaseDef.id === 'sync' ? (
                      <div className="space-y-0.5">
                        <p className="text-[10px] font-mono text-text-muted/50">{PLATFORM_LABELS[platform ?? 'plex'] ?? platform} library sync</p>
                        {libraryTrackCount !== undefined && (
                          <p className="text-[10px] font-mono text-text-muted/40">
                            Tracks indexed: {libraryTrackCount.toLocaleString()}
                          </p>
                        )}
                        {libraryLastSynced && (
                          <p className="text-[10px] font-mono text-text-muted/40">
                            Last synced: {libraryLastSynced}
                          </p>
                        )}
                      </div>
                    ) : (
                      <p className="text-[10px] font-mono text-text-muted/50">
                        Ownership, title normalization, canonical grouping
                      </p>
                    )}
                  </div>
                )}

                {/* Worker rows within phase */}
                {hasWorkers && phaseStats && phaseStats.total > 0 && (
                  <div className="mt-2 space-y-1 pl-3 border-l border-[#1a1a1a]">
                    {phaseDef.workers.map(w => {
                      const s = workerStats(workers, w.keys);
                      const isWorkerActive = running && (w.keys).some(k => activeWorkers.has(k));
                      if (!s.hasData) return null;
                      return (
                        <div key={w.key} className="flex items-center gap-2 text-[10px] font-mono">
                          {isWorkerActive && <Zap size={8} className="text-accent animate-pulse flex-shrink-0" />}
                          {!isWorkerActive && s.hasData && <CheckCircle size={8} className="text-accent/40 flex-shrink-0" />}
                          <span className={isWorkerActive ? 'text-text-secondary' : 'text-text-muted'}>{w.label}</span>
                          <span className="text-text-muted/50 ml-auto">
                            {s.found.toLocaleString()} / {s.total.toLocaleString()}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface SettingsPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function SettingsPage({ toast }: SettingsPageProps) {
  const { data: libraryStatus, refetch: refetchLibrary } = useApi(() => libraryApi.getStatus());
  const [platform, setPlatform] = useState<LibraryPlatform>('plex');

  useEffect(() => {
    if (libraryStatus?.platform) setPlatform(libraryStatus.platform as LibraryPlatform);
  }, [libraryStatus?.platform]);

  const [switchingBackend, setSwitchingBackend] = useState(false);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditReviewOpen, setAuditReviewOpen] = useState(false);
  const [auditReviewLoading, setAuditReviewLoading] = useState(false);
  const [auditReviewError, setAuditReviewError] = useState<string | null>(null);
  const [auditReviewItems, setAuditReviewItems] = useState<AuditItem[]>([]);
  const [auditInlineAlbumId, setAuditInlineAlbumId] = useState<string | null>(null);
  const [auditInlineLoading, setAuditInlineLoading] = useState(false);
  const [auditInlineError, setAuditInlineError] = useState<string | null>(null);
  const [auditInlineSaving, setAuditInlineSaving] = useState(false);
  const [auditInlineCandidates, setAuditInlineCandidates] = useState<Record<string, {
    itunes: AuditCandidateItem[];
    deezer: AuditCandidateItem[];
  }>>({});
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState<Settings | null>(null);

  const fetchEnabled = useSettingsStore(s => s.fetchEnabled);
  const { initFromApi, setFetchEnabled } = useSettingsStore();
  const [fetchToggling, setFetchToggling] = useState(false);

  // Enrichment state from global store — kept live by wsService
  const { running, workers, activeWorkers, startedAt, phase, reset } = useEnrichmentStore();

  // Elapsed timer — display concern only; driven by startedAt from store
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    if (!running || startedAt === null) { setElapsedMs(0); return; }
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [running, startedAt]);

  useEffect(() => {
    settingsApi.getApiKey().then(setApiKeyState).catch(() => {});
    settingsApi.get().then(s => {
      setSettingsStatus(s);
      initFromApi(s);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    libraryBrowseApi.getAudit({ per_page: 1 })
      .then(r => setAuditTotal(r.total))
      .catch(() => {});
  }, []);

  const loadAuditReviewItems = async (): Promise<AuditItem[]> => {
    setAuditReviewLoading(true);
    setAuditReviewError(null);
    try {
      const perPage = 200;
      let page = 1;
      let total = 0;
      let allItems: AuditItem[] = [];
      while (page <= 20) {
        const res = await libraryBrowseApi.getAudit({ page, per_page: perPage });
        if (page === 1) total = res.total ?? 0;
        allItems = allItems.concat(res.items ?? []);
        if ((res.items?.length ?? 0) < perPage || allItems.length >= total) break;
        page += 1;
      }
      setAuditReviewItems(allItems);
      setAuditTotal(total || allItems.length);
      return allItems;
    } catch (err) {
      setAuditReviewError(err instanceof Error ? err.message : 'Failed to load review items');
      setAuditReviewItems([]);
      return [];
    } finally {
      setAuditReviewLoading(false);
    }
  };

  const openAuditReviewModal = () => {
    setAuditReviewOpen(true);
    setAuditInlineAlbumId(null);
    setAuditInlineError(null);
    void loadAuditReviewItems();
  };

  const loadInlineCandidates = async (albumId: string) => {
    setAuditInlineLoading(true);
    setAuditInlineError(null);
    try {
      const [it, dz] = await Promise.all([
        libraryBrowseApi.getAuditCandidates({ album_id: albumId, source: 'itunes', limit: 15 }),
        libraryBrowseApi.getAuditCandidates({ album_id: albumId, source: 'deezer', limit: 15 }),
      ]);
      setAuditInlineCandidates(prev => ({
        ...prev,
        [albumId]: {
          itunes: it.candidates ?? [],
          deezer: dz.candidates ?? [],
        },
      }));
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to load candidates');
    } finally {
      setAuditInlineLoading(false);
    }
  };

  const toggleInlineReview = (albumId: string) => {
    if (auditInlineAlbumId === albumId) {
      setAuditInlineAlbumId(null);
      setAuditInlineError(null);
      return;
    }
    setAuditInlineAlbumId(albumId);
    setAuditInlineError(null);
    if (!auditInlineCandidates[albumId]) {
      void loadInlineCandidates(albumId);
    }
  };

  const refreshAuditAfterAction = async (albumId: string) => {
    const items = await loadAuditReviewItems();
    const stillExists = items.some(i => i.album_id === albumId);
    if (!stillExists) {
      setAuditInlineAlbumId(null);
      return;
    }
    await loadInlineCandidates(albumId);
  };

  const inlineConfirmCandidate = async (
    item: AuditItem,
    source: 'itunes' | 'deezer',
    candidateId: string,
  ) => {
    setAuditInlineSaving(true);
    setAuditInlineError(null);
    try {
      await libraryBrowseApi.confirmAuditItem({
        entity_type: 'album',
        entity_id: item.album_id,
        source,
        confirmed_id: candidateId,
      });
      toast.success(`${source === 'itunes' ? 'iTunes' : 'Deezer'} match confirmed`);
      await refreshAuditAfterAction(item.album_id);
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to confirm candidate');
    } finally {
      setAuditInlineSaving(false);
    }
  };

  const inlineRejectSource = async (
    item: AuditItem,
    source: 'itunes' | 'deezer',
  ) => {
    setAuditInlineSaving(true);
    setAuditInlineError(null);
    try {
      await libraryBrowseApi.rejectAuditItem({
        entity_type: 'album',
        entity_id: item.album_id,
        source,
      });
      toast.success(`${source === 'itunes' ? 'iTunes' : 'Deezer'} match rejected`);
      await refreshAuditAfterAction(item.album_id);
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to reject source');
    } finally {
      setAuditInlineSaving(false);
    }
  };

  const handlePlatformChange = async (p: LibraryPlatform) => {
    setPlatform(p);
    setSwitchingBackend(true);
    try {
      await settingsApi.setLibraryPlatform(p);
      toast.success(`Switched to ${PLATFORM_LABELS[p] ?? p}`);
      refetchLibrary();
    } catch {
      toast.error('Failed to switch platform');
    } finally {
      setSwitchingBackend(false);
    }
  };

  const handleClearHistory = async () => {
    try {
      await settingsApi.clearHistory();
      toast.success('History cleared');
    } catch {
      toast.error('Failed to clear history');
    }
    setConfirmClearHistory(false);
  };

  const handleResetDb = async () => {
    try {
      await settingsApi.resetDb();
      toast.success('Database reset');
    } catch {
      toast.error('Failed to reset database');
    }
    setConfirmResetDb(false);
  };

  const handleCopyApiKey = () => {
    if (!apiKey) return;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(apiKey).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }).catch(() => {
        fallbackCopy(apiKey);
      });
    } else {
      fallbackCopy(apiKey);
    }
  };

  const fallbackCopy = (text: string) => {
    const el = document.createElement('textarea');
    el.value = text;
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.focus();
    el.select();
    try {
      document.execCommand('copy');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } finally {
      document.body.removeChild(el);
    }
  };

  const handleFetchToggle = async () => {
    setFetchToggling(true);
    try {
      const newValue = !fetchEnabled;
      await settingsApi.setFetchEnabled(newValue);
      setFetchEnabled(newValue);
      toast.success(newValue ? 'Fetch enabled' : 'Fetch disabled');
    } catch {
      toast.error('Failed to update fetch setting');
    } finally {
      setFetchToggling(false);
    }
  };

  const handleRegenerateApiKey = async () => {
    setRegenerating(true);
    try {
      const newKey = await settingsApi.regenerateApiKey();
      setApiKeyState(newKey);
      setApiKey(newKey);
      toast.success('API key regenerated');
    } catch {
      toast.error('Failed to regenerate API key');
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <div className="py-8 space-y-10">
      <h1 className="page-title">Settings</h1>

      <section>
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-3">Connections</h2>
        <div className="grid grid-cols-3 gap-2">
          <ServiceCard
            name="Last.fm"
            icon={<Radio size={16} className="text-danger" />}
            configured={settingsStatus?.lastfm_configured}
            onTest={settingsApi.testLastfm}
          />
          <ServiceCard
            name={PLATFORM_LABELS[platform] ?? platform}
            icon={<span className="text-accent font-bold text-sm">{(PLATFORM_LABELS[platform] ?? platform)[0]}</span>}
            configured={platform === 'navidrome' ? Boolean(settingsStatus?.navidrome_configured) : settingsStatus?.plex_configured}
            onTest={platform === 'navidrome' ? settingsApi.testNavidrome : settingsApi.testPlex}
            extra={
              <div className="relative">
                <select
                  className="select"
                  value={libraryStatus?.platform ?? platform}
                  onChange={e => handlePlatformChange(e.target.value as LibraryPlatform)}
                  disabled={switchingBackend}
                >
                  <option value="plex">Plex</option>
                  <option value="jellyfin">Jellyfin</option>
                  <option value="navidrome">Navidrome</option>
                </select>
                {switchingBackend && (
                  <div className="absolute right-8 top-1/2 -translate-y-1/2 pointer-events-none">
                    <Loader2 size={13} className="animate-spin text-text-muted" />
                  </div>
                )}
              </div>
            }
          />
          <ServiceCard
            key={`library-${platform}`}
            name="SoulSync"
            icon={<Database size={16} className="text-accent" />}
            configured={settingsStatus?.soulsync_db_accessible}
            onTest={settingsApi.testSoulsync}
          />
          <ServiceCard
            name="Spotify"
            icon={<span className="text-success font-bold text-sm">S</span>}
            configured={settingsStatus?.spotify_configured}
            onTest={settingsApi.testSpotify}
          />
          <ServiceCard
            name="Fanart.tv"
            subtitle="optional"
            icon={<span className="text-[#e88c2a] font-bold text-sm">F</span>}
            configured={settingsStatus?.fanart_configured}
            onTest={settingsApi.testFanart}
          />
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-3">Capabilities</h2>
        <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex items-center justify-between">
          <div>
            <p className="text-text-primary text-sm font-medium">Enable Fetch</p>
            <p className="text-[#444] text-[10px] mt-0.5">
              Show download actions in the Forge. Requires a downloader plugin (Lidarr, Soulseek).
            </p>
          </div>
          <button
            onClick={handleFetchToggle}
            disabled={fetchToggling}
            className={`relative w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${
              fetchEnabled ? 'bg-accent' : 'bg-[#2a2a2a]'
            } ${fetchToggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
            aria-label={fetchEnabled ? 'Disable fetch' : 'Enable fetch'}
          >
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${
                fetchEnabled ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-1">Library Enrichment Pipeline</h2>
        <p className="text-[11px] text-[#444] mb-6">Syncs library, resolves IDs, enriches metadata from iTunes, Deezer, Last.fm, and Spotify</p>

        <PipelineOrchestrator
          running={running}
          workers={workers}
          activeWorkers={activeWorkers}
          elapsedMs={elapsedMs}
          phase={phase}
          libraryTrackCount={libraryStatus?.track_count}
          libraryLastSynced={libraryStatus?.last_synced}
          platform={platform}
          onRunFull={() => {
            reset();
            enrichmentApi.runFull()
              .then(() => {
                // Safety net: if pipeline completes before first WS event, reseed from REST
                setTimeout(() => {
                  enrichmentApi.status()
                    .then(s => {
                      if (!s.running && useEnrichmentStore.getState().running) {
                        useEnrichmentStore.getState().setFromStatus(s);
                      }
                    })
                    .catch(() => {});
                }, 3000);
              })
              .catch(() => toast.error('Failed to start enrichment'));
          }}
          onStop={() => {
            enrichmentApi.stop()
              .then(() => enrichmentApi.status())
              .then(useEnrichmentStore.getState().setFromStatus)
              .catch(() => {});
          }}
        />

        {auditTotal > 0 && (
          <div className="pt-2">
            <div className="flex items-center justify-between gap-3 bg-[#0e0e0e] border border-[#1a1a1a] p-3">
              <p className="text-xs text-[#666]">
                <span className="inline-flex items-center gap-1.5 text-amber-500 font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />
                  {auditTotal} item{auditTotal !== 1 ? 's' : ''} need review
                </span>
                {' '}— low-confidence matches flagged for manual confirmation.
              </p>
              <button
                onClick={openAuditReviewModal}
                className="btn-secondary text-xs"
              >
                Review
              </button>
            </div>
          </div>
        )}
      </section>

      {auditReviewOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="w-full max-w-5xl max-h-[85vh] bg-[#0d0d0d] border border-[#2a2a2a] flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a1a]">
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Review Low-Confidence Matches</h3>
                <p className="text-[11px] text-text-muted mt-0.5">
                  Review inline here, or open any album to use the full Fix Match view.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void loadAuditReviewItems()}
                  disabled={auditReviewLoading}
                  className="btn-secondary text-xs disabled:opacity-40"
                >
                  {auditReviewLoading ? 'Loading...' : 'Refresh'}
                </button>
                <button
                  onClick={() => {
                    setAuditReviewOpen(false);
                    setAuditInlineAlbumId(null);
                    setAuditInlineError(null);
                  }}
                  className="btn-secondary text-xs"
                >
                  Close
                </button>
              </div>
            </div>

            {auditReviewError && (
              <div className="px-4 py-2 text-xs text-danger border-b border-[#1a1a1a]">
                {auditReviewError}
              </div>
            )}

            <div className="flex-1 overflow-y-auto">
              <div className="grid grid-cols-[1.1fr_1.1fr_90px_120px_170px] gap-3 px-4 py-2 border-b border-[#1a1a1a] sticky top-0 bg-[#101010] z-10">
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Artist</span>
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Album</span>
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Confidence</span>
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">IDs</span>
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider text-right">Action</span>
              </div>

              {auditReviewLoading && (
                <div className="px-4 py-10 flex items-center justify-center text-text-muted text-xs">
                  <Loader2 size={14} className="animate-spin mr-2" />
                  Loading review queue...
                </div>
              )}

              {!auditReviewLoading && auditReviewItems.length === 0 && (
                <div className="px-4 py-10 text-center text-xs text-text-muted">
                  No review items currently flagged.
                </div>
              )}

              {!auditReviewLoading && auditReviewItems.map(item => {
                const ids = `${item.itunes_album_id ? 'iT' : ''}${item.itunes_album_id && item.deezer_id ? ' + ' : ''}${item.deezer_id ? 'DZ' : ''}` || 'none';
                const isOpen = auditInlineAlbumId === item.album_id;
                const candidates = auditInlineCandidates[item.album_id] ?? { itunes: [], deezer: [] };
                return (
                  <div key={`${item.album_id}:${item.artist_id}`} className="border-b border-[#151515]">
                    <div className="grid grid-cols-[1.1fr_1.1fr_90px_120px_170px] gap-3 px-4 py-2 items-center">
                      <span className="text-xs text-text-secondary truncate">{item.artist_name}</span>
                      <span className="text-xs text-text-primary truncate">{item.album_title}</span>
                      <span className="text-xs font-mono text-amber-400">{Math.round(item.match_confidence ?? 0)}%</span>
                      <span className="text-[11px] font-mono text-text-muted">{ids}</span>
                      <div className="text-right flex items-center justify-end gap-2">
                        <button
                          onClick={() => toggleInlineReview(item.album_id)}
                          className="inline-flex btn-secondary text-xs"
                        >
                          {isOpen ? 'Hide' : 'Inline'}
                        </button>
                        <Link
                          to="/library/album/$id"
                          params={{ id: item.album_id }}
                          onClick={() => setAuditReviewOpen(false)}
                          className="inline-flex btn-secondary text-xs"
                        >
                          Open
                        </Link>
                      </div>
                    </div>
                    {isOpen && (
                      <div className="px-4 pb-3">
                        {auditInlineError && (
                          <p className="text-xs text-danger mb-2">{auditInlineError}</p>
                        )}
                        {auditInlineLoading ? (
                          <div className="flex items-center text-xs text-text-muted py-3">
                            <Loader2 size={12} className="animate-spin mr-2" />
                            Loading candidates...
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                            {(['itunes', 'deezer'] as const).map(source => {
                              const sourceLabel = source === 'itunes' ? 'iTunes' : 'Deezer';
                              const currentId = source === 'itunes' ? item.itunes_album_id : item.deezer_id;
                              return (
                                <div key={`${item.album_id}:${source}`} className="border border-[#202020] bg-[#0f0f0f] p-2">
                                  <div className="flex items-center justify-between gap-2 mb-2">
                                    <span className="text-[11px] font-mono text-text-secondary uppercase tracking-wider">
                                      {sourceLabel}
                                    </span>
                                    <button
                                      onClick={() => void inlineRejectSource(item, source)}
                                      disabled={auditInlineSaving}
                                      className="btn-secondary text-[10px] disabled:opacity-40"
                                    >
                                      Reject current
                                    </button>
                                  </div>
                                  <p className="text-[10px] font-mono text-text-muted mb-2 truncate">
                                    current: {currentId || '(none)'}
                                  </p>
                                  <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                                    {candidates[source].length === 0 && (
                                      <p className="text-xs text-text-muted">No candidates</p>
                                    )}
                                    {candidates[source].map(c => {
                                      const isCurrent = !!currentId && String(currentId) === String(c.candidate_id);
                                      return (
                                        <div key={`${source}:${c.candidate_id}`} className="border border-[#242424] bg-[#111] px-2 py-1.5">
                                          <div className="flex items-start justify-between gap-2">
                                            <div className="min-w-0">
                                              <p className="text-xs text-text-primary truncate">{c.candidate_title}</p>
                                              <p className="text-[10px] font-mono text-text-muted">
                                                {Math.round((c.candidate_score ?? 0) * 100)}% • tracks {c.track_count ?? '-'}
                                              </p>
                                            </div>
                                            <div className="flex items-center gap-1">
                                              {isCurrent && (
                                                <span className="text-[9px] font-mono text-green-400 border border-green-400/30 px-1 py-0.5">
                                                  current
                                                </span>
                                              )}
                                              <button
                                                onClick={() => void inlineConfirmCandidate(item, source, c.candidate_id)}
                                                disabled={auditInlineSaving}
                                                className="px-1.5 py-1 text-[10px] bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
                                              >
                                                Confirm
                                              </button>
                                            </div>
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Security</h2>
        <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 space-y-3">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
              <Key size={14} className="text-text-muted" />
            </div>
            <div>
              <p className="text-text-primary text-sm font-medium">API Key</p>
              <p className="text-[#444] text-[10px]">Include as X-Api-Key header for external integrations</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 bg-[#141414] border border-[#222] px-3 py-2 font-mono text-xs text-text-muted truncate">
              {apiKey
                ? (apiKeyVisible ? apiKey : '•'.repeat(24))
                : <span className="text-[#333]">Loading…</span>}
            </div>
            <button
              onClick={() => setApiKeyVisible(v => !v)}
              className="btn-secondary p-2 flex-shrink-0"
              title={apiKeyVisible ? 'Hide key' : 'Show key'}
            >
              {apiKeyVisible ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button
              onClick={handleCopyApiKey}
              disabled={!apiKey}
              className="btn-secondary p-2 flex-shrink-0"
              title="Copy to clipboard"
            >
              {copied ? <CheckCircle size={14} className="text-success" /> : <Copy size={14} />}
            </button>
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleRegenerateApiKey}
              disabled={regenerating}
              className="btn-secondary text-xs flex items-center gap-1.5"
            >
              {regenerating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Regenerate
            </button>
          </div>
        </div>
      </section>

      <section className="border-t border-danger/20 pt-6">
        <button
          onClick={() => setDangerOpen(o => !o)}
          className="flex items-center justify-between w-full text-left"
        >
          <span className="text-danger text-xs font-semibold uppercase tracking-widest">Danger Zone</span>
          {dangerOpen ? <ChevronUp size={14} className="text-danger/60" /> : <ChevronDown size={14} className="text-danger/60" />}
        </button>

        {dangerOpen && (
          <div className="mt-5 border-l-2 border-danger/40 pl-4 space-y-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-text-primary text-sm font-semibold">Clear History</p>
                <p className="text-[#444] text-xs mt-0.5">Remove all New Music run history</p>
              </div>
              <button onClick={() => setConfirmClearHistory(true)} className="btn-danger text-sm flex-shrink-0">
                Clear History
              </button>
            </div>
            <div className="flex items-center justify-between gap-4 pt-4 border-t border-danger/10">
              <div>
                <p className="text-text-primary text-sm font-semibold">Reset Database</p>
                <p className="text-[#444] text-xs mt-0.5">Wipe all app data. This cannot be undone.</p>
              </div>
              <button onClick={() => setConfirmResetDb(true)} className="btn-danger text-sm flex-shrink-0">
                Reset DB
              </button>
            </div>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmClearHistory}
        title="Clear History?"
        description="All New Music run history will be permanently deleted. This cannot be undone."
        confirmLabel="Clear History"
        danger
        onConfirm={handleClearHistory}
        onCancel={() => setConfirmClearHistory(false)}
      />

      <ConfirmDialog
        open={confirmResetDb}
        title="Reset Database?"
        description="This will permanently delete ALL app data including playlists, queue items, and history. There is no recovery. Are you absolutely sure?"
        confirmLabel="Reset Everything"
        danger
        onConfirm={handleResetDb}
        onCancel={() => setConfirmResetDb(false)}
      />
    </div>
  );
}
