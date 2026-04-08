import type { EnrichmentSubsteps, EnrichmentWorkerKey, EnrichmentWorkerStatus } from '../../types';

export interface PhaseWorker {
  key: EnrichmentWorkerKey;
  label: string;
}

export interface PhaseSubstep {
  key: keyof EnrichmentSubsteps;
  label: string;
}

export interface Phase {
  id: string;
  label: string;
  backendPhases: readonly string[];
  workers: PhaseWorker[];
  displayType: 'text' | 'bar' | 'checklist';
  substeps?: PhaseSubstep[];
}

export const PIPELINE_PHASES: Phase[] = [
  {
    id: 'sync',
    label: 'Library Platform Sync',
    backendPhases: ['sync', 'tag_enrichment', 'artwork_repair'],
    workers: [],
    displayType: 'text',
  },
  {
    id: 'artist',
    label: 'Artist Resolution',
    backendPhases: ['id_itunes_deezer', 'id_parallel'],
    workers: [
      { key: 'itunes_artist', label: 'Artist: iTunes' },
      { key: 'deezer_artist', label: 'Artist: Deezer' },
      { key: 'spotify_artist', label: 'Artist: Spotify' },
      { key: 'lastfm_artist', label: 'Artist: Last.fm' },
      { key: 'artist_art', label: 'Artist: Artwork' },
    ],
    displayType: 'bar',
  },
  {
    id: 'album',
    label: 'Album Resolution',
    backendPhases: ['id_itunes_deezer'],
    workers: [
      { key: 'itunes_album', label: 'Album: iTunes' },
      { key: 'deezer_album', label: 'Album: Deezer' },
    ],
    displayType: 'bar',
  },
  {
    id: 'artwork',
    label: 'Album Artwork',
    backendPhases: ['album_art_local', 'album_art_cdn', 'album_art_prewarm'],
    workers: [
      { key: 'album_art_local', label: 'Local Files' },
      { key: 'album_art_cdn', label: 'CDN (Deezer / iTunes)' },
      { key: 'album_art_prewarm', label: 'Cache Warm' },
    ],
    displayType: 'bar',
  },
  {
    id: 'post',
    label: 'Post-Processing',
    backendPhases: ['ownership_sync', 'normalize_titles', 'missing_counts', 'canonical'],
    workers: [],
    displayType: 'checklist',
    substeps: [
      { key: 'ownership_sync', label: 'Ownership' },
      { key: 'normalize_titles', label: 'Title Normalization' },
      { key: 'missing_counts', label: 'Missing Counts' },
      { key: 'canonical', label: 'Canonical Grouping' },
    ],
  },
  {
    id: 'tags',
    label: 'Enrichment Tags',
    backendPhases: ['rich_data'],
    workers: [
      { key: 'itunes_rich', label: 'iTunes' },
      { key: 'deezer_rich', label: 'Deezer' },
      { key: 'spotify_genres', label: 'Spotify' },
      { key: 'lastfm_tags', label: 'Last.fm Tags' },
      { key: 'lastfm_stats', label: 'Last.fm Stats' },
      { key: 'deezer_artist_stats', label: 'Deezer Artist' },
      { key: 'similar_artists', label: 'Similar Artists' },
      { key: 'musicbrainz_rich', label: 'MusicBrainz' },
      { key: 'musicbrainz_album_rich', label: 'MusicBrainz Albums' },
    ],
    displayType: 'bar',
  },
];

export const ALL_BACKEND_KEYS = PIPELINE_PHASES.flatMap((phase) => phase.workers.map((worker) => worker.key));

export const WORKER_LABELS: Record<string, string> = Object.fromEntries(
  PIPELINE_PHASES.flatMap((phase) => phase.workers.map((worker) => [worker.key, worker.label])),
);

export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m.toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`;
}

/** Sum stats across one or more backend worker keys. */
export function workerStats(workers: Record<string, EnrichmentWorkerStatus>, keys: readonly string[]) {
  let found = 0;
  let notFound = 0;
  let errors = 0;
  let pending = 0;
  for (const key of keys) {
    const worker = workers[key];
    if (!worker) continue;
    found += worker.found;
    notFound += worker.not_found;
    errors += worker.errors;
    pending += worker.pending;
  }
  const total = found + notFound + errors + pending;
  const foundPct = total > 0 ? (found / total) * 100 : 0;
  const notFoundPct = total > 0 ? (notFound / total) * 100 : 0;
  const errorPct = total > 0 ? (errors / total) * 100 : 0;
  const processedPct = foundPct + notFoundPct + errorPct;
  return { found, notFound, errors, pending, total, foundPct, notFoundPct, errorPct, processedPct, hasData: total > 0 };
}
