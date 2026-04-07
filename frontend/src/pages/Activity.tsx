import { useState } from 'react';
import { Music2, RefreshCw } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { acquisitionApi } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';
import { Spinner, EmptyState, ApiErrorBanner } from '../components/common';
import type { QueueItem, AcquisitionStatus } from '../types';

interface ActivityPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

const STATUS_FILTERS: Array<{ value: AcquisitionStatus | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'pending', label: 'Pending' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'found', label: 'Found' },
  { value: 'failed', label: 'Failed' },
  { value: 'skipped', label: 'Skipped' },
];

export function ActivityPage({ toast }: ActivityPageProps) {
  const [filter, setFilter] = useState<AcquisitionStatus | 'all'>('all');
  const [checking, setChecking] = useState(false);
  const { data: queue, loading, error: queueError, refetch } = useApi(() => acquisitionApi.getQueue(filter === 'all' ? undefined : filter), filter);
  const { data: stats } = useApi(() => acquisitionApi.getStats());

  const handleCheckNow = async () => {
    setChecking(true);
    try {
      await acquisitionApi.checkNow();
      toast.success('Acquisition check started');
      setTimeout(refetch, 2000);
    } catch {
      toast.error('Failed to start check');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="py-8 space-y-8">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="page-title mb-1">Activity</h1>
          <p className="text-text-dim text-sm">Acquisition queue and fetch status</p>
        </div>
        <button
          onClick={handleCheckNow}
          disabled={checking}
          className="btn-secondary flex items-center gap-2 text-sm"
        >
          {checking ? <Spinner size={14} /> : <RefreshCw size={14} />}
          Check Now
        </button>
      </div>

      {stats && (
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
          {[
            { label: 'Pending', value: stats.pending, color: 'text-warning' },
            { label: 'Submitted', value: stats.submitted, color: 'text-accent' },
            { label: 'Found', value: stats.found, color: 'text-success' },
            { label: 'Failed', value: stats.failed, color: 'text-danger' },
            { label: 'Skipped', value: stats.skipped, color: 'text-text-muted' },
          ].map(s => (
            <div key={s.label} className="bg-base border border-border-subtle p-3 text-center">
              <p className={`text-xl font-bold tabular-nums ${s.color}`}>{s.value}</p>
              <p className="text-text-dim text-xs mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-1 flex-wrap">
        {STATUS_FILTERS.map(f => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-3 py-1.5 text-xs font-medium rounded-sm transition-colors ${
              filter === f.value
                ? 'bg-accent text-black'
                : 'bg-surface text-text-muted hover:text-text-secondary hover:bg-surface-skeleton'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {queueError ? (
        <ApiErrorBanner error={queueError} onRetry={refetch} />
      ) : loading ? (
        <div className="space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 py-3 border-b border-surface">
              <div className="w-10 h-10 animate-pulse bg-surface-skeleton flex-shrink-0" />
              <div className="flex-1 space-y-2">
                <div className="h-3 animate-pulse bg-surface-skeleton rounded-sm w-40" />
                <div className="h-3 animate-pulse bg-surface-skeleton rounded-sm w-28" />
              </div>
              <div className="h-5 w-16 animate-pulse bg-surface-skeleton rounded-sm" />
            </div>
          ))}
        </div>
      ) : !queue || queue.length === 0 ? (
        <EmptyState
          icon={<Music2 size={32} />}
          message="No items in queue"
          sub={filter === 'all' ? 'Run New Music in The Forge to populate the queue' : `No ${filter} items`}
        />
      ) : (
        <div className="space-y-0">
          {queue.map((item: QueueItem) => (
            <div
              key={item.id}
              className="flex items-center gap-3 py-3 border-b border-surface hover:bg-base transition-colors"
            >
              <div className="w-10 h-10 flex-shrink-0 bg-surface-skeleton flex items-center justify-center">
                <Music2 size={13} className="text-text-faint" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-text-primary text-sm font-medium truncate">{item.album}</p>
                <p className="text-text-muted text-xs truncate">{item.artist}</p>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className="badge-muted text-[10px] capitalize">{item.kind}</span>
                <StatusBadge status={item.status} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
