export function ConfidenceBadge({ value }: { value: number }) {
  const color = value >= 90 ? 'text-green-400' : value >= 70 ? 'text-warning-text' : 'text-danger';
  return <span className={`font-mono text-[10px] ${color}`}>{value}%</span>;
}
