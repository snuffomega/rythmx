import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, Loader2, RefreshCw, Database, Radio, ChevronDown, ChevronUp, Key, Eye, EyeOff, Copy, Play, Square, Clock, Zap } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { settingsApi, libraryApi, libraryBrowseApi, setApiKey, enrichmentApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useEnrichmentStore } from '../stores/useEnrichmentStore';
import type { LibraryPlatform, EnrichmentWorkerStatus, Settings } from '../types';

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
}

function ServiceCard({ name, subtitle, icon, configured, onTest }: ServiceRowProps) {
  const [status, setStatus] = useState<'idle' | 'testing' | 'connected' | 'error'>('idle');
  const [message, setMessage] = useState<string | null>(null);

  const handleTest = async () => {
    setStatus('testing');
    setMessage(null);
    try {
      const result = await onTest();
      setStatus(result.connected ? 'connected' : 'error');
      setMessage(result.message ?? null);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Connection failed');
    }
  };

  return (
    <div className="relative bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex flex-col gap-3">
      {configured && (
        <span className="absolute top-2.5 right-2.5 w-2 h-2 rounded-full bg-accent" title="Configured" />
      )}
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
        <div>
          <p className="text-text-primary text-sm font-medium">{name}</p>
          {subtitle && <p className="text-[#444] text-[10px]">{subtitle}</p>}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          {status === 'connected' && (
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-success flex-shrink-0" />
              <span className="text-success text-xs truncate">{message ?? 'Connected'}</span>
            </div>
          )}
          {status === 'error' && (
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-danger flex-shrink-0" />
              <span className="text-danger text-xs truncate">{message ?? 'Failed'}</span>
            </div>
          )}
          {status === 'idle' && (
            <span className="text-[#444] text-xs">Not tested</span>
          )}
          {status === 'testing' && (
            <span className="text-text-muted text-xs">Testing…</span>
          )}
        </div>

        <button
          onClick={handleTest}
          disabled={status === 'testing'}
          className="btn-ghost flex items-center gap-1.5 text-xs flex-shrink-0"
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
  onRunFull: () => void;
  onStop: () => void;
}

function PipelineOrchestrator({ running, workers, activeWorkers, elapsedMs, phase, onRunFull, onStop }: PipelineOrchestratorProps) {
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
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold tracking-tight text-text-primary">Enrichment Pipeline</h2>
        {(running || elapsedMs > 0) && (
          <div className="flex items-center gap-2 text-sm font-mono text-text-muted">
            <Clock size={14} />
            <span>{formatElapsed(elapsedMs)}</span>
          </div>
        )}
      </div>

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
                  <p className="text-[10px] font-mono text-text-muted/50 mt-0.5">
                    {phaseDef.id === 'sync' ? 'Plex library sync' : 'Ownership, title normalization, canonical grouping'}
                  </p>
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
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState<Settings | null>(null);

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
    settingsApi.get().then(setSettingsStatus).catch(() => {});
  }, []);

  useEffect(() => {
    libraryBrowseApi.getAudit({ per_page: 1 })
      .then(r => setAuditTotal(r.total))
      .catch(() => {});
  }, []);

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
    navigator.clipboard.writeText(apiKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
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
            name="Plex"
            icon={<span className="text-accent font-bold text-sm">P</span>}
            configured={settingsStatus?.plex_configured}
            onTest={settingsApi.testPlex}
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
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-6">Library & Enrichment</h2>

        {/* Step 1 — Sync */}
        <div className="mb-6 p-4 bg-[#0e0e0e] border border-[#1a1a1a]">
          <div className="mb-3">
            <p className="text-xs font-mono text-text-muted uppercase tracking-wider mb-0.5">Step 1 — Sync from Plex</p>
            <p className="text-[11px] text-[#444]">Reads your Plex library into the local database</p>
          </div>

          <div className="mb-4">
            <label className="label">Library Platform</label>
            <div className="relative mt-1">
              <select
                className="select w-full"
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
          </div>

          <div className="text-[11px] text-[#444] space-y-0.5">
            {libraryStatus?.track_count !== undefined && (
              <p>{libraryStatus.track_count.toLocaleString()} tracks indexed</p>
            )}
            {libraryStatus?.last_synced && (
              <p>Last synced: {libraryStatus.last_synced}</p>
            )}
          </div>
        </div>

        {/* Pipeline (unified — sync + enrich in one run) */}
        <div className="mb-4">
          <p className="text-xs font-mono text-text-muted uppercase tracking-wider mb-1">Pipeline</p>
          <p className="text-[11px] text-[#444] mb-4">Syncs library, resolves IDs, enriches metadata from iTunes, Deezer, Last.fm, and Spotify</p>
          <PipelineOrchestrator
            running={running}
            workers={workers}
            activeWorkers={activeWorkers}
            elapsedMs={elapsedMs}
            phase={phase}
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
        </div>

        {auditTotal > 0 && (
          <div className="pt-2">
            <p className="text-xs text-[#666]">
              <span className="inline-flex items-center gap-1.5 text-amber-500 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />
                {auditTotal} item{auditTotal !== 1 ? 's' : ''} need review
              </span>
              {' '}— low-confidence matches flagged for manual confirmation.{' '}
              <span className="text-[#444]">Audit UI coming in Phase 13c.</span>
            </p>
          </div>
        )}
      </section>

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
                <p className="text-[#444] text-xs mt-0.5">Remove all Cruise Control run history</p>
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
        description="All Cruise Control run history will be permanently deleted. This cannot be undone."
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
