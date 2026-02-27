import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from 'lucide-react';
import type { Toast } from '../types';

interface ToastContainerProps {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}

const ICONS: Record<Toast['type'], React.ReactNode> = {
  success: <CheckCircle size={15} className="text-success flex-shrink-0" />,
  error: <AlertCircle size={15} className="text-danger flex-shrink-0" />,
  warning: <AlertTriangle size={15} className="text-warning flex-shrink-0" />,
  info: <Info size={15} className="text-accent flex-shrink-0" />,
};

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className="flex items-center gap-3 px-4 py-3 bg-[#1a1a1a] border border-[#2a2a2a] shadow-lg min-w-64 max-w-sm pointer-events-auto"
        >
          {ICONS[toast.type]}
          <p className="text-text-primary text-sm flex-1">{toast.message}</p>
          <button
            onClick={() => onDismiss(toast.id)}
            className="text-text-muted hover:text-text-secondary transition-colors flex-shrink-0"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
