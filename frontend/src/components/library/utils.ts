// ---------------------------------------------------------------------------
// Library utility types and pure helper functions
// Extracted from Library.tsx for reuse across library sub-components.
// ---------------------------------------------------------------------------

export type Tab = 'artists' | 'albums' | 'tracks';
export type ViewMode = 'grid' | 'list';

export function formatDuration(ms: number | null): string {
  if (!ms) return '--:--';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function firstTag(json: string | null): string {
  try {
    const tags = JSON.parse(json ?? '[]') as string[];
    return tags[0] ?? '';
  } catch {
    return '';
  }
}

export type KindGroup<T> = { kind: string; label: string; items: T[] };

export function groupByKind<T extends { kind?: string | null; record_type?: string | null }>(
  items: T[],
  kindField: 'kind' | 'record_type' = 'record_type'
): KindGroup<T>[] {
  const order = ['album', 'ep', 'single', 'compilation'];
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const k = (kindField === 'kind' ? item.kind : item.record_type)?.toLowerCase() || 'album';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(item);
  }
  return order
    .filter(k => groups.has(k))
    .map(k => ({
      kind: k,
      label: k === 'ep' ? 'EPs' : k.charAt(0).toUpperCase() + k.slice(1) + 's',
      items: groups.get(k)!,
    }));
}

export function parseTags(json: string | null): string[] {
  try { return (JSON.parse(json ?? '[]') as string[]).filter(Boolean); }
  catch { return []; }
}

export function mergeUniqueTags(a: string | null, b: string | null): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const t of [...parseTags(a), ...parseTags(b)]) {
    const k = t.toLowerCase();
    if (!seen.has(k)) { seen.add(k); result.push(t); }
  }
  return result;
}

export function formatCount(n: number | null): string | null {
  if (n == null) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
}

export const BACKEND_COLORS: Record<string, string> = {
  plex: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  navidrome: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  jellyfin: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
};
