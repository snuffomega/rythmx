import { useState, useEffect, useRef } from 'react';
import { CheckCircle, XCircle, Loader2, RefreshCw, Database, Radio, ChevronDown, ChevronUp, Key, Eye, EyeOff, Copy, Play, Square, Clock, Zap } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { settingsApi, libraryApi, libraryBrowseApi, imageServiceApi, setApiKey, enrichmentApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { useWebSocket } from '../hooks/useWebSocket';
import type { LibraryPlatform, EnrichmentPipelineStatus, EnrichmentWorkerStatus, WsEnrichmentProgress, Settings } from '../types';

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
// PipelineOrchestrator — 4-stage pipeline view replacing SettingsEnrichmentCard
// ---------------------------------------------------------------------------

// "library" aggregates all enrich_library sub-sources: itunes_artist + deezer_artist
// (artist confidence validation) + itunes + deezer (album-level ID enrichment).
// Counts = total artist + album identity work across both iTunes and Deezer.
const STAGE_GROUPS = [
  { id: 'identity', label: 'Identity Matching',   description: 'iTunes + Deezer IDs, Spotify ID, Last.fm ID', workers: ['library', 'spotify_id', 'lastfm_id'] },
  { id: 'metadata', label: 'Metadata Enrichment', description: 'iTunes Rich, Deezer Rich',                     workers: ['itunes_rich', 'deezer_rich'] },
  { id: 'genres',   label: 'Genre & Tagging',     description: 'Spotify Genres, Last.fm Tags',                 workers: ['spotify_genres', 'lastfm_tags'] },
  { id: 'audio',    label: 'Audio Analysis',      description: 'Last.fm Stats, Deezer BPM',                    workers: ['lastfm_stats', 'deezer_bpm'] },
] as const;

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m.toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

interface PipelineOrchestratorProps {
  status: EnrichmentPipelineStatus | null;
  activeWorkers: Set<string>;
  elapsedMs: number;
  onRunFull: () => void;
  onStop: () => void;
}

function PipelineOrchestrator({ status, activeWorkers, elapsedMs, onRunFull, onStop }: PipelineOrchestratorProps) {
  const running = status?.running ?? false;
  const [showStageDetail, setShowStageDetail] = useState(false);

  // Per-stage aggregates
  const stageData = STAGE_GROUPS.map(group => {
    const workers = group.workers.map(k => status?.workers?.[k]).filter((w): w is EnrichmentWorkerStatus => !!w);
    const found = workers.reduce((s, w) => s + w.found, 0);
    const notFound = workers.reduce((s, w) => s + w.not_found, 0);
    const errors = workers.reduce((s, w) => s + w.errors, 0);
    const pending = workers.reduce((s, w) => s + w.pending, 0);
    const total = found + notFound + errors + pending;

    const anyWorking = running && group.workers.some(k => activeWorkers.has(k));
    const hasData = found + notFound + errors > 0;
    const allDone = hasData && pending === 0;
    const stageStatus: 'idle' | 'working' | 'complete' = anyWorking ? 'working' : allDone ? 'complete' : hasData ? 'working' : 'idle';

    const foundPct = total > 0 ? (found / total) * 100 : 0;
    const notFoundPct = total > 0 ? (notFound / total) * 100 : 0;
    const errorPct = total > 0 ? (errors / total) * 100 : 0;
    const processedPct = foundPct + notFoundPct + errorPct;

    return { group, found, notFound, errors, pending, total, stageStatus, foundPct, notFoundPct, errorPct, processedPct };
  });

  // Overall progress
  const totalFound = stageData.reduce((s, d) => s + d.found, 0);
  const totalProcessed = stageData.reduce((s, d) => s + d.found + d.notFound + d.errors, 0);
  const totalItems = stageData.reduce((s, d) => s + d.total, 0);
  const overallPct = totalItems > 0 ? (totalProcessed / totalItems) * 100 : 0;
  const totalErrors = stageData.reduce((s, d) => s + d.errors, 0);

  return (
    <div data-testid="pipeline-orchestrator" className="max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-text-primary">Metadata Enrichment Pipeline</h2>
          <p className="text-xs font-mono text-text-muted mt-1">Enrich your library with metadata from multiple sources</p>
        </div>
        {(running || elapsedMs > 0) && (
          <div className="flex items-center gap-2 text-sm font-mono text-text-muted">
            <Clock size={14} />
            <span>{formatElapsed(elapsedMs)}</span>
          </div>
        )}
      </div>

      {/* Overall progress — always visible */}
      <div className="mb-6 p-4 bg-surface rounded-sm border border-[#1a1a1a]">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-mono text-text-muted uppercase tracking-wider">Overall Progress</span>
          <span className="text-xs font-mono text-text-secondary">{overallPct.toFixed(1)}%</span>
        </div>
        <div className="h-1.5 bg-surface-highlight rounded-full overflow-hidden">
          <div className="h-full bg-accent transition-all duration-500 ease-out" style={{ width: `${overallPct}%` }} />
        </div>
        <div className="flex items-center gap-4 mt-2 text-[10px] font-mono text-text-muted">
          <span>{totalProcessed.toLocaleString()} / {totalItems.toLocaleString()} items</span>
          <span className="text-accent">{totalFound.toLocaleString()} enriched</span>
          {totalErrors > 0 && <span className="text-red-400">{totalErrors} errors</span>}
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3 mb-6">
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
        {!running && totalProcessed > 0 && (
          <span className="text-xs font-mono text-text-muted">
            Last run: {totalFound.toLocaleString()} enriched
          </span>
        )}
      </div>

      {/* Stage detail toggle */}
      <button
        onClick={() => setShowStageDetail(v => !v)}
        className="flex items-center gap-1.5 text-xs font-mono text-text-muted hover:text-text-secondary transition-colors mb-3"
      >
        {showStageDetail ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {showStageDetail ? 'Hide stage details' : 'Show stage details'}
      </button>

      {/* Stage cards */}
      {showStageDetail && <div className="space-y-3">
        {stageData.map(({ group, found, notFound, errors, total, stageStatus, foundPct, notFoundPct, errorPct, processedPct }, idx) => {
          const isWorking = stageStatus === 'working';
          const isComplete = stageStatus === 'complete';
          return (
            <div
              key={group.id}
              className={`relative p-5 rounded-sm border transition-all duration-500 ${
                isWorking
                  ? 'bg-surface border-accent/30 shadow-[0_0_20px_rgba(212,245,60,0.05)]'
                  : isComplete
                    ? 'bg-surface/50 border-[#1a1a1a]'
                    : 'bg-surface/30 border-[#141414]'
              }`}
            >
              {/* Stage header */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-medium transition-colors duration-300 ${
                    isComplete ? 'bg-accent/20 text-accent' : isWorking ? 'bg-accent/10 text-accent' : 'bg-surface-highlight text-text-muted'
                  }`}>
                    {isComplete ? <CheckCircle size={14} /> : idx + 1}
                  </div>
                  <div>
                    <h3 className={`text-sm font-medium tracking-tight transition-colors duration-300 ${
                      isWorking ? 'text-text-primary' : isComplete ? 'text-text-secondary' : 'text-text-muted'
                    }`}>{group.label}</h3>
                    <p className="text-[11px] font-mono text-text-muted/70 mt-0.5">{group.description}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {isWorking && (
                    <span className="flex items-center gap-1.5 text-[10px] font-mono text-accent uppercase tracking-wider">
                      <Zap size={10} className="animate-pulse" />
                      Working
                    </span>
                  )}
                  {isComplete && (
                    <span className="flex items-center gap-1.5 text-[10px] font-mono text-text-muted uppercase tracking-wider">
                      <CheckCircle size={10} />
                      Complete
                    </span>
                  )}
                </div>
              </div>

              {/* 3-segment progress bar */}
              <div className="relative h-2 bg-surface-highlight rounded-full overflow-hidden mb-3">
                <div className="absolute top-0 left-0 h-full bg-accent transition-all duration-500 ease-out" style={{ width: `${foundPct}%` }} />
                <div className="absolute top-0 h-full bg-text-muted/50 transition-all duration-500 ease-out" style={{ left: `${foundPct}%`, width: `${notFoundPct}%` }} />
                <div className="absolute top-0 h-full bg-red-500/70 transition-all duration-500 ease-out" style={{ left: `${foundPct + notFoundPct}%`, width: `${errorPct}%` }} />
                {isWorking && (
                  <div className="absolute top-0 h-full overflow-hidden" style={{ left: `${processedPct}%`, width: `${100 - processedPct}%` }}>
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent animate-shimmer" />
                  </div>
                )}
              </div>

              {/* Stats row */}
              <div className="flex items-center gap-4 text-[11px] font-mono">
                <span className="text-accent">{found.toLocaleString()} found</span>
                <span className="text-text-muted">{notFound.toLocaleString()} not found</span>
                {errors > 0 && <span className="text-red-400">{errors.toLocaleString()} errors</span>}
                <span className="ml-auto text-text-muted/60">{(found + notFound + errors).toLocaleString()} / {total.toLocaleString()}</span>
              </div>
            </div>
          );
        })}
      </div>}
    </div>
  );
}

interface SettingsPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function SettingsPage({ toast }: SettingsPageProps) {
  const { data: libraryStatus, loading: libraryLoading, refetch: refetchLibrary } = useApi(() => libraryApi.getStatus());
  const [platform, setPlatform] = useState<LibraryPlatform>('plex');

  useEffect(() => {
    if (libraryStatus?.platform) setPlatform(libraryStatus.platform as LibraryPlatform);
  }, [libraryStatus?.platform]);
  const [syncing, setSyncing] = useState(false);
  const [enrichStatus, setEnrichStatus] = useState<EnrichmentPipelineStatus | null>(null);
  const [activeWorkers, setActiveWorkers] = useState<Set<string>>(new Set());
  const [elapsedMs, setElapsedMs] = useState(0);
  const elapsedStartRef = useRef<number | null>(null);
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [switchingBackend, setSwitchingBackend] = useState(false);
  const [auditTotal, setAuditTotal] = useState(0);
  const [warmingCache, setWarmingCache] = useState(false);
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmClearImageCache, setConfirmClearImageCache] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState<Settings | null>(null);

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

  const handleSync = async () => {
    setSyncing(true);
    try {
      await libraryApi.sync();
      toast.success('Library sync started');
      setTimeout(refetchLibrary, 2000);
    } catch {
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  // Enrichment status — initial snapshot
  useEffect(() => {
    enrichmentApi.status().then(s => {
      setEnrichStatus(s);
      if (s?.running && s?.started_at) {
        // Compute elapsed from server-provided start time so mid-run page load shows accurate timer.
        elapsedStartRef.current = new Date(s.started_at).getTime();
      } else if (s?.running) {
        elapsedStartRef.current = Date.now();
      }
    }).catch(() => {});
  }, []);

  // Elapsed timer — runs while enrichment is active
  useEffect(() => {
    const running = enrichStatus?.running ?? false;
    if (running) {
      if (!elapsedStartRef.current) elapsedStartRef.current = Date.now();
      elapsedTimerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - (elapsedStartRef.current ?? Date.now()));
      }, 1000);
    } else {
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current);
        elapsedTimerRef.current = null;
      }
    }
    return () => {
      if (elapsedTimerRef.current) clearInterval(elapsedTimerRef.current);
    };
  }, [enrichStatus?.running]);

  // Enrichment status — WS-driven updates
  useWebSocket((event, payload) => {
    if (event === 'enrichment_progress') {
      const p = payload as WsEnrichmentProgress;
      setActiveWorkers(prev => new Set([...prev, p.worker]));
      setEnrichStatus(prev => ({
        running: p.running,
        workers: {
          ...(prev?.workers ?? {}),
          [p.worker]: { found: p.found, not_found: p.not_found, errors: p.errors, pending: p.pending },
        },
      }));
      if (p.running && !elapsedStartRef.current) {
        elapsedStartRef.current = Date.now();
      }
    } else if (event === 'enrichment_complete') {
      enrichmentApi.status().then(setEnrichStatus).catch(() => {});
      setActiveWorkers(new Set());
      elapsedStartRef.current = null;
    } else if (event === 'enrichment_stopped') {
      setEnrichStatus(prev => prev ? { ...prev, running: false } : prev);
      setActiveWorkers(new Set());
      elapsedStartRef.current = null;
    }
  });

  const handleClearHistory = async () => {
    try {
      await settingsApi.clearHistory();
      toast.success('History cleared');
    } catch {
      toast.error('Failed to clear history');
    }
    setConfirmClearHistory(false);
  };

  const handleWarmCache = async () => {
    setWarmingCache(true);
    try {
      const result = await imageServiceApi.warmCache(40);
      if (result.submitted > 0) {
        toast.success(`Submitted ${result.submitted} image${result.submitted === 1 ? '' : 's'} for background fetch`);
      } else {
        toast.success('Cache is already warm');
      }
    } catch {
      toast.error('Failed to warm cache');
    } finally {
      setWarmingCache(false);
    }
  };

  const handleClearImageCache = async () => {
    try {
      await settingsApi.clearImageCache();
      toast.success('Image cache cleared');
    } catch {
      toast.error('Failed to clear image cache');
    }
    setConfirmClearImageCache(false);
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
      setApiKey(newKey); // update the in-memory + localStorage key for subsequent requests
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
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-xs font-mono text-text-muted uppercase tracking-wider mb-0.5">Step 1 — Sync from Plex</p>
              <p className="text-[11px] text-[#444]">Reads your Plex library into the local database</p>
            </div>
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

          <div className="flex items-center justify-between">
            <div className="text-[11px] text-[#444] space-y-0.5">
              {libraryStatus?.track_count !== undefined && (
                <p>{libraryStatus.track_count.toLocaleString()} tracks indexed</p>
              )}
              {libraryStatus?.last_synced && (
                <p>Last synced: {libraryStatus.last_synced}</p>
              )}
            </div>
            <button
              onClick={handleSync}
              disabled={syncing}
              className="btn-secondary flex items-center gap-2 text-sm flex-shrink-0"
            >
              {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              Sync Now
            </button>
          </div>
        </div>

        {/* Step 1 → Step 2 connector */}
        <div className="flex items-center gap-2 mb-6 pl-2">
          <div className="w-px h-6 bg-[#2a2a2a] ml-1" />
          <p className="text-[10px] font-mono text-[#444]">auto-triggers Step 2 after sync completes (30s delay)</p>
        </div>

        {/* Step 2 — Enrich */}
        <div className="mb-4">
          <p className="text-xs font-mono text-text-muted uppercase tracking-wider mb-1">Step 2 — Enrich Metadata</p>
          <p className="text-[11px] text-[#444] mb-4">Fetches IDs, genres, BPM, and tags from iTunes, Deezer, Last.fm, and Spotify</p>
          <PipelineOrchestrator
            status={enrichStatus}
            activeWorkers={activeWorkers}
            elapsedMs={elapsedMs}
            onRunFull={() => {
              setEnrichStatus(prev => ({ running: true, workers: {} }));
              setActiveWorkers(new Set());
              elapsedStartRef.current = Date.now();
              setElapsedMs(0);
              enrichmentApi.runFull()
                .then(() => enrichmentApi.status().then(setEnrichStatus))
                .catch(() => toast.error('Failed to start enrichment'));
            }}
            onStop={() => {
              enrichmentApi.stop()
                .then(() => setEnrichStatus(prev => prev ? { ...prev, running: false } : prev))
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
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Images</h2>
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <button
              onClick={handleWarmCache}
              disabled={warmingCache}
              className="btn-secondary flex items-center gap-1.5 text-sm flex-shrink-0"
            >
              {warmingCache ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Warm Now
            </button>
            <div>
              <p className="text-text-primary text-sm font-semibold">Warm Image Cache</p>
              <p className="text-[#444] text-xs mt-0.5">Pre-fetch artwork in the background</p>
            </div>
          </div>
          <div className="w-px h-10 bg-[#1a1a1a] flex-shrink-0 hidden sm:block" />
          <div className="flex items-center gap-3">
            <button onClick={() => setConfirmClearImageCache(true)} className="btn-secondary text-sm flex-shrink-0">
              Clear Cache
            </button>
            <div>
              <p className="text-text-primary text-sm font-semibold">Clear Image Cache</p>
              <p className="text-[#444] text-xs mt-0.5">Force all artwork to re-fetch</p>
            </div>
          </div>
        </div>
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
        open={confirmClearImageCache}
        title="Clear Image Cache?"
        description="All cached artwork URLs will be removed. Images will re-fetch from external sources on next page load."
        confirmLabel="Clear Cache"
        onConfirm={handleClearImageCache}
        onCancel={() => setConfirmClearImageCache(false)}
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
