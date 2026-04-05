export function ConfidenceBadge({ value }: { value: number }) {
  const color = value >= 90 ? 'text-green-400' : value >= 70 ? 'text-yellow-400' : 'text-red-400';
  return <span className={`font-mono text-[10px] ${color}`}>{value}%</span>;
}
