type BadgeStatus = 'owned' | 'queued' | 'skipped' | 'pending' | 'submitted' | 'found' | 'failed';

interface StatusBadgeProps {
  status: BadgeStatus | string;
}

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  owned: { label: 'Owned', className: 'badge-success' },
  queued: { label: 'Queued', className: 'badge-accent' },
  skipped: { label: 'Skipped', className: 'badge-muted' },
  pending: { label: 'Pending', className: 'badge-warning' },
  submitted: { label: 'Submitted', className: 'badge-accent' },
  found: { label: 'Found', className: 'badge-success' },
  failed: { label: 'Failed', className: 'badge-danger' },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const cfg = STATUS_CONFIG[status] ?? { label: status, className: 'badge-muted' };
  return <span className={cfg.className}>{cfg.label}</span>;
}
