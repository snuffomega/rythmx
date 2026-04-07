import { useState, useEffect } from 'react';
import { CheckCircle, ChevronDown, ChevronUp, Play, Square, Clock, Zap } from 'lucide-react';
import { enrichmentApi } from '../../services/api';
import { useEnrichmentStore } from '../../stores/useEnrichmentStore';
import { PIPELINE_PHASES, ALL_BACKEND_KEYS, WORKER_LABELS, formatElapsed, workerStats } from './utils';
import type { LibraryPlatform } from '../../types';

// ---------------------------------------------------------------------------
// ProgressBar — stacked progress bar used for overall and per-phase/worker bars
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// PipelineOrchestrator — hybrid layout (default: results summary, expanded: phase-grouped DAG)
// ---------------------------------------------------------------------------

interface PipelineOrchestratorProps {
  libraryTrackCount?: number;
  libraryLastSynced?: string;
  platform?: LibraryPlatform;
  onRunFull: () => void;
  onStop: () => void;
}

function PipelineOrchestrator({ libraryTrackCount, libraryLastSynced, platform, onRunFull, onStop }: PipelineOrchestratorProps) {
  const { running, workers, activeWorkers, phase } = useEnrichmentStore();
  const [showStages, setShowStages] = useState(false);

  // Elapsed timer — display concern only; driven by startedAt from store
  const startedAt = useEnrichmentStore(s => s.startedAt);
  const [elapsedMs, setElapsedMs] = useState(0);
  useEffect(() => {
    if (!running || startedAt === null) { setElapsedMs(0); return; }
    const id = setInterval(() => setElapsedMs(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [running, startedAt]);

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
      <div className="mb-4 p-4 bg-surface rounded-sm border border-border-subtle">
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
        <div className="space-y-2 border-l border-border-subtle ml-1 pl-4">
          {PIPELINE_PHASES.map((phaseDef, idx) => {
            const isPhaseActive = running && phaseIndex === idx;
            const anyDataExists = totals.total > 0;
            const isPhaseDone = running
              ? phaseIndex > idx
              : phaseDef.workers.length > 0
                ? phaseDef.workers.some(w => workerStats(workers, w.keys).hasData)
                : anyDataExists;
            const isPhaseWaiting = running && phaseIndex < idx;
            const hasWorkers = phaseDef.workers.length > 0;

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
                  : isPhaseDone ? 'bg-surface/50 border-border-subtle'
                  : 'bg-surface/30 border-border-subtle'
                }`}
              >
                {/* Phase header */}
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    {isPhaseActive && <Zap size={10} className="text-accent animate-pulse" />}
                    {isPhaseDone && !isPhaseActive && <CheckCircle size={10} className="text-accent/60" />}
                    {isPhaseWaiting && <span className="w-2.5 h-2.5 rounded-full border border-border-strong inline-block" />}
                    {!running && !isPhaseDone && <span className="w-2.5 h-2.5 rounded-full border border-border-strong inline-block" />}
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
                        <p className="text-[10px] font-mono text-text-muted/50">Library platform sync</p>
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
                  <div className="mt-2 space-y-1 pl-3 border-l border-border-subtle">
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

// ---------------------------------------------------------------------------
// EnrichmentSection
// ---------------------------------------------------------------------------

interface EnrichmentSectionProps {
  platform: LibraryPlatform;
  libraryTrackCount?: number;
  libraryLastSynced?: string;
  auditTotal: number;
  onOpenAuditReview: () => void;
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function EnrichmentSection({ platform, libraryTrackCount, libraryLastSynced, auditTotal, onOpenAuditReview, toast }: EnrichmentSectionProps) {
  const { reset } = useEnrichmentStore();

  const handleRunFull = () => {
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
  };

  const handleStop = () => {
    enrichmentApi.stop()
      .then(() => enrichmentApi.status())
      .then(useEnrichmentStore.getState().setFromStatus)
      .catch(() => {});
  };

  return (
    <section className="border-t border-border-subtle pt-8">
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-1">Library Enrichment Pipeline</h2>
      <p className="text-[11px] text-text-dim mb-6">Syncs library, resolves IDs, enriches metadata from iTunes, Deezer, Last.fm, and Spotify</p>

      <PipelineOrchestrator
        libraryTrackCount={libraryTrackCount}
        libraryLastSynced={libraryLastSynced}
        platform={platform}
        onRunFull={handleRunFull}
        onStop={handleStop}
      />

      {auditTotal > 0 && (
        <div className="pt-2">
          <div className="flex items-center gap-3 bg-base border border-border-subtle p-3">
            <button
              onClick={onOpenAuditReview}
              className="btn-secondary text-xs flex-shrink-0"
            >
              Review
            </button>
            <p className="text-xs text-text-muted">
              <span className="inline-flex items-center gap-1.5 text-amber-500 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />
                {auditTotal} item{auditTotal !== 1 ? 's' : ''} need review
              </span>
              {' '}— low-confidence matches flagged for manual confirmation.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
