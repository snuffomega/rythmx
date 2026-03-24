import { Loader2, CheckCircle, X, Minus, Clock } from 'lucide-react';

export function AcqIcon({ status }: { status?: string | null }) {
  if (status === 'submitted') return <Loader2 size={10} className="text-accent animate-spin flex-shrink-0" aria-label="Fetching" />;
  if (status === 'found')     return <CheckCircle size={10} className="text-success flex-shrink-0" aria-label="Found" />;
  if (status === 'failed')    return <X size={10} className="text-danger flex-shrink-0" aria-label="Failed" />;
  if (status === 'skipped')   return <Minus size={10} className="text-text-muted flex-shrink-0" aria-label="Skipped" />;
  if (status === 'pending')   return <Clock size={10} className="text-text-muted flex-shrink-0" aria-label="Queued" />;
  return <Clock size={10} className="text-[#2a2a2a] flex-shrink-0" aria-label="Not queued" />;
}
