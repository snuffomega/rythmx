import { RowSkeleton } from '../Skeleton';
import type { HistoryItem } from '../../types';

function HistoryBadge({ status, reason }: { status: string; reason?: string }) {
  const key = reason ? `${status}:${reason}` : status;
  const cfg: Record<string, { label: string; cls: string }> = {
    owned:                   { label: 'Owned',          cls: 'badge-success' },
    queued:                  { label: 'Queued',         cls: 'badge-accent'  },
    'queued:already_queued': { label: 'Already queued', cls: 'badge-muted'   },
    'skipped:playlist_mode': { label: 'Not owned',      cls: 'badge-muted'   },
    skipped:                 { label: 'Skipped',        cls: 'badge-muted'   },
  };
  const c = cfg[key] ?? cfg[status] ?? { label: status, cls: 'badge-muted' };
  return <span className={c.cls}>{c.label}</span>;
}

interface CCHistoryProps {
  history: HistoryItem[] | null;
  loading: boolean;
}

export function CCHistory({ history, loading }: CCHistoryProps) {
  return (
    <section className="border-t border-[#1a1a1a] pt-8">
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Last Run History</h2>
      {loading ? (
        <div className="space-y-0">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="border-b border-[#1a1a1a]"><RowSkeleton /></div>
          ))}
        </div>
      ) : !history || history.length === 0 ? (
        <p className="text-text-muted text-sm py-8">No history yet — run Cruise Control to get started</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#1a1a1a]">
                <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-0 py-3">Artist</th>
                <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3">Album</th>
                <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3">Status</th>
                <th className="text-left text-[#444] font-medium text-xs uppercase tracking-widest px-4 py-3 hidden sm:table-cell">Date</th>
              </tr>
            </thead>
            <tbody>
              {history.map((item, i) => (
                <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#141414] transition-colors">
                  <td className="px-0 py-3 text-text-primary font-medium">{item.artist}</td>
                  <td className="px-4 py-3 text-text-secondary">{item.album}</td>
                  <td className="px-4 py-3"><HistoryBadge status={item.status} reason={item.reason} /></td>
                  <td className="px-4 py-3 text-[#444] hidden sm:table-cell">
                    {new Date(item.date).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
