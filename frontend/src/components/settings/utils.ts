import type { EnrichmentWorkerStatus } from '../../types';

// Pipeline phases matching backend DAG — grouped by service, not task.
// Each worker entry lists one or more backend keys to sum (handles the
// id_itunes_deezer combined "library" key during live runs vs individual
// sub-source keys from REST/completion payloads).
export const PIPELINE_PHASES = [
  { id: 'sync', label: 'Library Platform Sync', backendPhases: ['sync'], workers: [] },
  { id: 'identity', label: 'Identity Resolution', backendPhases: ['id_itunes_deezer', 'id_parallel'], workers: [
    { key: 'itunes_ids',   keys: ['itunes_artist', 'itunes'],  label: 'iTunes',   desc: 'Artist & album identity matching' },
    { key: 'deezer_ids',   keys: ['deezer_artist', 'deezer'],  label: 'Deezer',   desc: 'Artist & album identity matching' },
    { key: 'library',      keys: ['library'],                   label: 'iTunes / Deezer',  desc: 'Combined live progress' },
    { key: 'spotify_id',   keys: ['spotify_id'],                label: 'Spotify',  desc: 'Artist ID from Spotify' },
    { key: 'lastfm_id',    keys: ['lastfm_id'],                 label: 'Last.fm',  desc: 'Artist MBID from Last.fm' },
    { key: 'artist_art',   keys: ['artist_art'],                label: 'Artist Artwork',  desc: 'Fanart.tv + Deezer photos' },
  ]},
  { id: 'post', label: 'Post-Processing', backendPhases: ['ownership_sync', 'normalize_titles', 'missing_counts', 'canonical'], workers: [] },
  { id: 'rich', label: 'Rich Metadata', backendPhases: ['rich_data'], workers: [
    { key: 'itunes_rich',    keys: ['itunes_rich'],    label: 'iTunes Enrichment',   desc: 'Release date, genre, label' },
    { key: 'deezer_rich',    keys: ['deezer_rich'],    label: 'Deezer Enrichment',   desc: 'Record type, album art' },
    { key: 'spotify_genres', keys: ['spotify_genres'], label: 'Spotify Enrichment',  desc: 'Artist genre tags' },
    { key: 'lastfm_tags',   keys: ['lastfm_tags'],    label: 'Last.fm Enrichment',  desc: 'Community tags' },
    { key: 'lastfm_stats',  keys: ['lastfm_stats'],   label: 'Last.fm Stats',       desc: 'Playcount, listeners' },
  ]},
] as const;

// All backend keys across all phases (for overall totals — deduped).
export const ALL_BACKEND_KEYS = [...new Set(
  PIPELINE_PHASES.flatMap(p => p.workers.flatMap(w => w.keys))
)];

// Human-readable label for the currently active worker (keyed by backend worker name).
export const WORKER_LABELS: Record<string, string> = {
  library: 'iTunes/Deezer IDs',
  itunes_artist: 'iTunes', itunes: 'iTunes', deezer_artist: 'Deezer', deezer: 'Deezer',
  spotify_id: 'Spotify', lastfm_id: 'Last.fm', artist_art: 'Artist Artwork',
  itunes_rich: 'iTunes Enrichment', deezer_rich: 'Deezer Enrichment',
  spotify_genres: 'Spotify Enrichment', lastfm_tags: 'Last.fm Enrichment', lastfm_stats: 'Last.fm Stats',
};

export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m.toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

/** Sum stats across one or more backend worker keys. */
export function workerStats(workers: Record<string, EnrichmentWorkerStatus>, keys: readonly string[]) {
  let found = 0, notFound = 0, errors = 0, pending = 0;
  for (const k of keys) {
    const w = workers[k];
    if (!w) continue;
    found += w.found; notFound += w.not_found; errors += w.errors; pending += w.pending;
  }
  const total = found + notFound + errors + pending;
  const foundPct = total > 0 ? (found / total) * 100 : 0;
  const notFoundPct = total > 0 ? (notFound / total) * 100 : 0;
  const errorPct = total > 0 ? (errors / total) * 100 : 0;
  const processedPct = foundPct + notFoundPct + errorPct;
  return { found, notFound, errors, pending, total, foundPct, notFoundPct, errorPct, processedPct, hasData: total > 0 };
}
