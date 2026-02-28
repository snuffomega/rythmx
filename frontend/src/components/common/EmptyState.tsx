import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  message: string;
  sub?: string;
}

export function EmptyState({ icon, message, sub }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {icon && <div className="mb-3 text-[#222]">{icon}</div>}
      <p className="text-[#444] text-sm">{message}</p>
      {sub && <p className="text-[#333] text-xs mt-1">{sub}</p>}
    </div>
  );
}
