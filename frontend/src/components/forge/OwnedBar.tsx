export function OwnedBar({ owned, total }: { owned: number; total: number }) {
  const pct = total > 0 ? Math.round((owned / total) * 100) : 0;
  const color = pct > 20 ? 'bg-accent' : 'bg-danger';
  return (
    <div className="flex items-center gap-3">
      <span className="text-text-muted text-xs whitespace-nowrap">
        {owned} owned ({pct}%)
      </span>
      <div className="flex-1 h-0.5 bg-[#1e1e1e]">
        <div className={`h-full ${color} transition-all duration-300`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
