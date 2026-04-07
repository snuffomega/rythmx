import { useState, useEffect } from 'react';
import { RowSkeleton } from '../Skeleton';
import { forgeApi } from '../../services/api';
import type { PipelineRun } from '../../types';

function StatusBadge({ status }: { status: PipelineRun['status'] }) {
  const cfg = {
    running:   { label: 'Running',   cls: 'badge-accent' },
    completed: { label: 'Completed', cls: 'badge-success' },
    error:     { label: 'Error',     cls: 'badge-danger' },
  } as const;
  const c = cfg[status] ?? { label: status, cls: 'badge-muted' };
  return <span className={c.cls}>{c.label}</span>;
}

function formatDuration(secs: number | null) {
  if (secs == null) return '—';
  if (secs < 60) return `${Math.round(secs)}s`;
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
}

interface PipelineRunHistoryProps {
  pipelineType: 'new_music' | 'custom_discovery';
}

export function PipelineRunHistory({ pipelineType }: PipelineRunHistoryProps) {
  const [runs, setRuns] = useState<PipelineRun[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    forgeApi.getPipelineHistory(pipelineType, 20)
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, [pipelineType]);

  return (
    <section className="border-t border-border-subtle pt-8">
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Pipeline Runs</h2>
      {loading ? (
        <div className="space-y-0">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="border-b border-border-subtle"><RowSkeleton /></div>
          ))}
        </div>
      ) : !runs || runs.length === 0 ? (
        <p className="text-text-muted text-sm py-4">No runs recorded yet</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border-subtle">
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-0 py-3">Started</th>
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-4 py-3">Mode</th>
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-4 py-3">Status</th>
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-4 py-3 hidden sm:table-cell">Duration</th>
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-4 py-3 hidden md:table-cell">Trigger</th>
                <th className="text-left text-text-dim font-medium text-xs uppercase tracking-widest px-4 py-3 hidden lg:table-cell">Summary</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => {
                const summary = run.summary_json ? (() => { try { return JSON.parse(run.summary_json); } catch { return null; } })() : null;
                return (
                  <tr key={run.id} className="border-b border-border-subtle hover:bg-surface-skeleton transition-colors">
                    <td className="px-0 py-3 text-text-primary font-medium">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-text-secondary capitalize">{run.run_mode}</td>
                    <td className="px-4 py-3"><StatusBadge status={run.status} /></td>
                    <td className="px-4 py-3 text-text-dim hidden sm:table-cell">{formatDuration(run.run_duration)}</td>
                    <td className="px-4 py-3 text-text-dim hidden md:table-cell capitalize">{run.triggered_by}</td>
                    <td className="px-4 py-3 text-text-dim text-xs hidden lg:table-cell">
                      {summary
                        ? `${summary.artists_checked ?? 0} artists · ${summary.new_releases ?? 0} releases · ${summary.owned ?? 0} owned`
                        : run.error_message
                        ? <span className="text-danger truncate max-w-xs block">{run.error_message}</span>
                        : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
