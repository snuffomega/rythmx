import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  message: string;
  sub?: string;
}

export function EmptyState({ icon, message, sub }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {icon && <div className="mb-3 text-text-faint">{icon}</div>}
      <p className="text-text-dim text-sm">{message}</p>
      {sub && <p className="text-text-faint text-xs mt-1">{sub}</p>}
    </div>
  );
}
