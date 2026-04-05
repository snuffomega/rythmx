import { BACKEND_COLORS } from '../utils';

export function SourceChip({ backend }: { backend: string | null }) {
  if (!backend) return null;
  const cls = BACKEND_COLORS[backend] ?? 'bg-[#1a1a1a] text-text-muted border-[#333]';
  return (
    <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${cls}`}>
      {backend}
    </span>
  );
}
