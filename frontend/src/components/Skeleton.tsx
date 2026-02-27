export function RowSkeleton() {
  return (
    <div className="flex items-center gap-3 px-0 py-3">
      <div className="w-10 h-10 bg-[#1a1a1a] animate-pulse flex-shrink-0" />
      <div className="flex-1 space-y-2">
        <div className="h-3 bg-[#1a1a1a] animate-pulse rounded-sm w-40" />
        <div className="h-3 bg-[#1a1a1a] animate-pulse rounded-sm w-28" />
      </div>
      <div className="w-16 h-5 bg-[#1a1a1a] animate-pulse rounded-sm" />
    </div>
  );
}

export function CardSkeleton() {
  return (
    <div className="space-y-2">
      <div className="aspect-square bg-[#1a1a1a] animate-pulse" />
      <div className="h-3 bg-[#1a1a1a] animate-pulse rounded-sm w-3/4" />
      <div className="h-3 bg-[#1a1a1a] animate-pulse rounded-sm w-1/2" />
    </div>
  );
}
