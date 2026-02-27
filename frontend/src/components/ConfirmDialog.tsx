import { AlertTriangle } from 'lucide-react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, description, confirmLabel = 'Confirm', danger, onConfirm, onCancel }: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative bg-[#111] border border-[#222] w-full max-w-sm p-6 space-y-4">
        <div className="flex items-start gap-3">
          {danger && <AlertTriangle size={18} className="text-danger flex-shrink-0 mt-0.5" />}
          <div>
            <h3 className="text-text-primary font-bold text-sm">{title}</h3>
            <p className="text-text-muted text-sm mt-1 leading-relaxed">{description}</p>
          </div>
        </div>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="btn-secondary text-sm px-4 py-1.5">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`text-sm px-4 py-1.5 font-semibold rounded-sm transition-colors ${
              danger ? 'btn-danger' : 'btn-primary'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
