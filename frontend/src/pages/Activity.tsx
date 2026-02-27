import { useState } from 'react';
import { Music2, RefreshCw, Loader2, Plus } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { acquisitionApi } from '../services/api';
import { StatusBadge } from '../components/StatusBadge';
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
  const { data: queue, loading, refetch } = useApi(() => acquisitionApi.getQueue(filter === 'all' ? undefined : filter));
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
          <p className="text-[#444] text-sm">Acquisition queue and download status</p>
        </div>
        <button
          onClick={handleCheckNow}
          disabled={checking}
          className="btn-secondary flex items-center gap-2 text-sm"
        >
          {checking ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
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
            <div key={s.label} className="bg-[#0d0d0d] border border-[#161616] p-3 text-center">
              <p className={`text-xl font-bold tabular-nums ${s.color}`}>{s.value}</p>
              <p className="text-[#444] text-xs mt-0.5">{s.label}</p>
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
                : 'bg-[#111] text-text-muted hover:text-text-secondary hover:bg-[#161616]'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 py-3 border-b border-[#111]">
              <div className="w-10 h-10 animate-pulse bg-[#141414] flex-shrink-0" />
              <div className="flex-1 space-y-2">
                <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-40" />
                <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-28" />
              </div>
              <div className="h-5 w-16 animate-pulse bg-[#141414] rounded-sm" />
            </div>
          ))}
        </div>
      ) : !queue || queue.length === 0 ? (
        <div className="text-center py-16">
          <Music2 size={32} className="text-[#222] mx-auto mb-3" />
          <p className="text-[#444] text-sm">No items in queue</p>
          <p className="text-[#333] text-xs mt-1">
            {filter === 'all' ? 'Run Cruise Control to populate the queue' : `No ${filter} items`}
          </p>
        </div>
      ) : (
        <div className="space-y-0">
          {queue.map((item: QueueItem) => (
            <div
              key={item.id}
              className="flex items-center gap-3 py-3 border-b border-[#111] hover:bg-[#0d0d0d] transition-colors"
            >
              <div className="w-10 h-10 flex-shrink-0 bg-[#141414] flex items-center justify-center">
                <Music2 size={13} className="text-[#333]" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-text-primary text-sm font-medium truncate">{item.album}</p>
                <p className="text-[#555] text-xs truncate">{item.artist}</p>
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
