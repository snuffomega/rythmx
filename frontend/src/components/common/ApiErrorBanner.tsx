interface ApiErrorBannerProps {
  error: string;
  onRetry?: () => void;
}

export function ApiErrorBanner({ error, onRetry }: ApiErrorBannerProps) {
  return (
    <div className="flex items-center gap-3 py-3 px-4 rounded bg-[#1a0a0a] border border-danger/20 text-danger text-sm">
      <span className="flex-1">{error}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="text-xs text-danger/70 hover:text-danger underline underline-offset-2 shrink-0"
        >
          Retry
        </button>
      )}
    </div>
  );
}
