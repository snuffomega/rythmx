import type {
  Period,
  AcquisitionStatus,
  QueueItem,
  AcquisitionStats,
  Artist,
  Track,
  TopAlbum,
  LibraryStatus,
  ConnectionStatus,
  Settings,
  ReleaseKind,
  ForgeDiscoveryConfig,
  ForgeDiscoveryResult,
  LibArtist,
  LibAlbum,
  LibTrack,
  LibArtistDetail,
  LibAlbumDetail,
  AuditResponse,
  EnrichmentPipelineStatus,
  EnrichmentStopResponse,
  ConnectionVerifyResult,
  ConnectionStatusResult,
  ConnectionServiceStatus,
  ReleaseDetail,
  ReleaseTrack,
  ReleaseSibling,
  UserReleasePrefs,
  SimilarArtist,
  NewMusicConfig,
  DiscoveredRelease,
  ForgeBuild,
  ForgeBuildSource,
  ForgeBuildStatus,
  LibPlaylist,
  LibPlaylistTrack,
} from '../types';

const BASE_URL = '/api/v1';

// ── API key management ────────────────────────────────────────────────────────
// Seeded from /auth/bootstrap on app load, then injected into every request.
// Stored in localStorage so it survives page refreshes without a round-trip.

let _apiKey = '';
try { _apiKey = localStorage.getItem('rythmx_api_key') ?? ''; } catch { /* private mode */ }

export function getApiKey(): string { return _apiKey; }

export function setApiKey(key: string): void {
  _apiKey = key;
  try { localStorage.setItem('rythmx_api_key', key); } catch { /* ignore */ }
}

export async function initApiKey(): Promise<void> {
  // Always fetch from bootstrap — ensures stale localStorage keys are replaced
  // (e.g. after a fresh DB install). Bootstrap is public and fast.
  try {
    const res = await fetch(`${BASE_URL}/auth/bootstrap`);
    if (!res.ok) return;
    const data = await res.json() as { status: string; api_key?: string };
    if (data.status === 'ok' && data.api_key) setApiKey(data.api_key);
  } catch { /* network down — fall back to cached key */ }
}

// ── Error class ───────────────────────────────────────────────────────────────

// Thrown when the backend returns { status: 'error', error/message: '...' }.
// Catch blocks can use `instanceof ApiError` to distinguish API vs network errors.
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly httpStatus = 0,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(_apiKey ? { 'X-Api-Key': _apiKey } : {}),
      ...options?.headers,
    },
    ...options,
  });
  if (res.status === 401) {
    // Stale key (e.g. fresh DB install). Clear and re-bootstrap, then retry once.
    setApiKey('');
    await initApiKey();
    const retry = await fetch(`${BASE_URL}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(_apiKey ? { 'X-Api-Key': _apiKey } : {}),
        ...options?.headers,
      },
      ...options,
    });
    if (!retry.ok) {
      const err = await retry.text().catch(() => 'Request failed');
      throw new ApiError(err || `HTTP ${retry.status}`, retry.status);
    }
    const retryData: unknown = await retry.json();
    if (retryData && typeof retryData === 'object' && (retryData as Record<string, unknown>).status === 'error') {
      const d = retryData as Record<string, unknown>;
      throw new ApiError(typeof d.error === 'string' ? d.error : typeof d.message === 'string' ? d.message : 'API error');
    }
    return retryData as T;
  }
  if (!res.ok) {
    const err = await res.text().catch(() => 'Request failed');
    throw new ApiError(err || `HTTP ${res.status}`, res.status);
  }
  const data: unknown = await res.json();
  if (data && typeof data === 'object' && (data as Record<string, unknown>).status === 'error') {
    const d = data as Record<string, unknown>;
    throw new ApiError(
      typeof d.error === 'string' ? d.error
        : typeof d.message === 'string' ? d.message
        : 'API error',
    );
  }
  return data as T;
}

export const acquisitionApi = {
  getQueue: (status?: AcquisitionStatus | 'all', playlist?: string) => {
    const params = new URLSearchParams();
    if (status && status !== 'all') params.set('status', status);
    if (playlist) params.set('playlist', playlist);
    const qs = params.toString();
    return request<{ status: string; items: QueueItem[] }>(
      `/acquisition/queue${qs ? `?${qs}` : ''}`
    ).then(r => r.items);
  },
  addToQueue: (item: {
    artist: string;
    album: string;
    release_date?: string;
    kind: ReleaseKind;
    source?: string;
  }) =>
    request<{ status: string }>('/acquisition/queue', {
      method: 'POST',
      body: JSON.stringify({
        artist_name: item.artist,
        album_title: item.album,
        release_date: item.release_date,
        kind: item.kind,
        source: item.source,
      }),
    }),
  getStats: () =>
    request<{ status: string } & AcquisitionStats>('/acquisition/stats')
      .then(({ status: _s, ...stats }) => stats as AcquisitionStats),
  checkNow: () =>
    request<{ status: string }>('/acquisition/check-now', { method: 'POST' }),
};

export const statsApi = {
  getTopArtists: (period: Period = '1month', limit = 20) =>
    request<{ status: string; artists: Artist[] }>(
      `/stats/top-artists?period=${period}&limit=${limit}`
    ).then(r => r.artists),
  getTopTracks: (period: Period = '1month', limit = 20) =>
    request<{ status: string; tracks: Track[] }>(
      `/stats/top-tracks?period=${period}&limit=${limit}`
    ).then(r => r.tracks),
  getTopAlbums: (period: Period = '1month', limit = 20) =>
    request<{ status: string; albums: TopAlbum[] }>(
      `/stats/top-albums?period=${period}&limit=${limit}`
    ).then(r => r.albums),
  getLovedArtists: () =>
    request<{ status: string; artists: Artist[] }>('/stats/loved-artists')
      .then(r => r.artists),
};

export const settingsApi = {
  get: () =>
    request<{ status: string } & Settings>('/settings')
      .then(({ status: _s, ...rest }) => rest as Settings),
  testLastfm: () =>
    request<ConnectionStatus>('/settings/test-lastfm', { method: 'POST' }),
  testPlex: () =>
    request<ConnectionStatus>('/settings/test-plex', { method: 'POST' }),
  testNavidrome: () =>
    request<ConnectionStatus>('/settings/test-soulsync', { method: 'POST' }),
  testSoulsync: () =>
    request<ConnectionStatus>('/settings/test-soulsync', { method: 'POST' }),
  testSpotify: () =>
    request<ConnectionStatus>('/settings/test-spotify', { method: 'POST' }),
  testFanart: () =>
    request<ConnectionStatus>('/settings/test-fanart', { method: 'POST' }),
  setLibraryPlatform: (platform: 'plex' | 'jellyfin' | 'navidrome') =>
    request<{ status: string; platform: string }>('/settings/library-platform', {
      method: 'POST',
      body: JSON.stringify({ platform }),
    }),
  clearHistory: () =>
    request<{ status: string }>('/settings/clear-history', { method: 'POST' }),
  resetDb: () =>
    request<{ status: string }>('/settings/reset-db', { method: 'POST' }),
  getApiKey: () =>
    request<{ status: string; api_key: string }>('/settings/api-key')
      .then(r => r.api_key),
  regenerateApiKey: () =>
    request<{ status: string; api_key: string }>('/settings/regenerate-api-key', { method: 'POST' })
      .then(r => r.api_key),
  setFetchEnabled: (enabled: boolean) =>
    request<{ status: string; fetch_enabled: boolean }>('/settings/fetch-enabled', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }).then(r => r.fetch_enabled),
};

export const connectionsApi = {
  verifyAll: () =>
    request<ConnectionVerifyResult>('/connections/verify', { method: 'POST' }),
  verifyService: (service: string) =>
    request<ConnectionServiceStatus>(`/connections/verify/${service}`, { method: 'POST' }),
  getStatus: () =>
    request<ConnectionStatusResult>('/connections/status'),
};

export const libraryApi = {
  getStatus: () =>
    request<{ status: string } & LibraryStatus>('/library/status')
      .then(({ status: _s, ...rest }) => rest as LibraryStatus),
};

export const forgeDiscoveryApi = {
  getConfig: () =>
    request<{ status: string; config: ForgeDiscoveryConfig }>('/forge/discovery/config')
      .then(r => r.config),
  saveConfig: (config: Partial<ForgeDiscoveryConfig>) =>
    request<{ status: string }>('/forge/discovery/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  run: (config: Partial<ForgeDiscoveryConfig>) =>
    request<{ status: string; artists: ForgeDiscoveryResult[]; artists_found: number }>(
      '/forge/discovery/run',
      {
        method: 'POST',
        body: JSON.stringify(config),
      }
    ),
  getResults: () =>
    request<{ status: string; artists: ForgeDiscoveryResult[] }>('/forge/discovery/results')
      .then(r => r.artists),
};

export const libraryBrowseApi = {
  getArtists: (p: { q?: string; page?: number; per_page?: number; backend?: string; decade?: number | null; region?: string | null; letter?: string | null } = {}) => {
    const qs = new URLSearchParams(
      Object.entries({ per_page: '50', ...p }).filter(([, v]) => v != null && v !== '') as [string, string][]
    ).toString();
    return request<{ status: string; artists: LibArtist[]; total: number; page: number }>(`/library/artists?${qs}`);
  },
  getArtistFilterOptions: () =>
    request<{ status: string; decades: number[]; regions: string[] }>('/library/artists/filter-options'),
  getArtist: (id: string) =>
    request<{ status: string } & LibArtistDetail>(`/library/artists/${encodeURIComponent(id)}`),
  getSimilarArtists: (id: string) =>
    request<{ status: string; similar: SimilarArtist[] }>(`/library/artists/${encodeURIComponent(id)}/similar`),
  getAlbums: (p: { q?: string; page?: number; per_page?: number; backend?: string; record_type?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries({ per_page: '50', ...p }).filter(([, v]) => v != null) as [string, string][]
    ).toString();
    return request<{ status: string; albums: LibAlbum[]; total: number; page: number }>(`/library/albums?${qs}`);
  },
  getAlbum: (id: string) =>
    request<{ status: string } & LibAlbumDetail>(`/library/albums/${encodeURIComponent(id)}`),
  getTracks: (p: { q?: string; page?: number; per_page?: number; artist_id?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries({ per_page: '100', ...p }).filter(([, v]) => v != null) as [string, string][]
    ).toString();
    return request<{ status: string; tracks: LibTrack[]; total: number; page: number }>(`/library/tracks?${qs}`);
  },
  setArtistCover: (artistId: string, coverUrl: string) =>
    request<{ status: string }>(`/library/artists/${encodeURIComponent(artistId)}/cover`, {
      method: 'POST',
      body: JSON.stringify({ cover_url: coverUrl }),
    }),
  rateTrack: (id: string, rating: number) =>
    request<{ status: string; track_id: string; rating: number }>(`/library/tracks/${encodeURIComponent(id)}/rating`, {
      method: 'PATCH',
      body: JSON.stringify({ rating }),
    }),
  getAudit: (p: { page?: number; per_page?: number } = {}) => {
    const qs = new URLSearchParams(
      Object.entries({ per_page: '50', ...p }).filter(([, v]) => v != null) as [string, string][]
    ).toString();
    return request<AuditResponse>(`/library/audit?${qs}`);
  },
  confirmAuditItem: (body: { entity_type: string; entity_id: string; source: string; confirmed_id: string }) =>
    request<{ status: string }>('/library/audit/confirm', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  rejectAuditItem: (body: { entity_type: string; entity_id: string; source: string }) =>
    request<{ status: string }>('/library/audit/reject', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getRelease: (id: string) =>
    request<{ status: string; release: ReleaseDetail; tracks: ReleaseTrack[]; siblings: ReleaseSibling[] }>(`/library/releases/${encodeURIComponent(id)}`),
  getReleasePrefs: (id: string) =>
    request<{ status: string; prefs: UserReleasePrefs | null }>(`/library/releases/${encodeURIComponent(id)}/prefs`),
  updateReleasePrefs: (id: string, data: { dismissed?: boolean; priority?: number; notes?: string }) =>
    request<{ status: string }>(`/library/releases/${encodeURIComponent(id)}/prefs`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
};

export const enrichmentApi = {
  status: () =>
    request<EnrichmentPipelineStatus & { status: string }>('/library/enrich/status'),
  runFull: (batch_size = 50) =>
    request<{ status: string; message: string }>('/library/enrich/full', {
      method: 'POST',
      body: JSON.stringify({ batch_size }),
    }),
  stop: () =>
    request<EnrichmentStopResponse>('/library/enrich/stop', { method: 'POST' }),
};

export const forgeApi = {
  getPipelineHistory: (pipelineType?: string, limit = 50) => {
    const params = new URLSearchParams();
    if (pipelineType) params.set('pipeline_type', pipelineType);
    params.set('limit', String(limit));
    return request<{ status: string; runs: import('../types').PipelineRun[] }>(
      `/forge/pipeline-history?${params}`
    ).then(r => r.runs);
  },
};

export const forgeNewMusicApi = {
  getConfig: () =>
    request<{ status: string; config: NewMusicConfig }>('/forge/new-music/config')
      .then(r => r.config),
  saveConfig: (config: Partial<NewMusicConfig>) =>
    request<{ status: string }>('/forge/new-music/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  run: (config?: Partial<NewMusicConfig>) =>
    request<{ status: string; releases: DiscoveredRelease[]; artists_checked: number; releases_found: number }>(
      '/forge/new-music/run',
      { method: 'POST', body: JSON.stringify(config ?? {}) }
    ),
  getResults: () =>
    request<{ status: string; releases: DiscoveredRelease[] }>('/forge/new-music/results')
      .then(r => r.releases),
};

export const forgeBuildsApi = {
  list: (source?: ForgeBuildSource, limit = 100) => {
    const params = new URLSearchParams();
    if (source) params.set('source', source);
    params.set('limit', String(limit));
    const qs = params.toString();
    return request<{ status: string; builds: ForgeBuild[] }>(`/forge/builds?${qs}`)
      .then(r => r.builds);
  },
  get: (id: string) =>
    request<{ status: string; build: ForgeBuild }>(`/forge/builds/${encodeURIComponent(id)}`)
      .then(r => r.build),
  update: (id: string, data: {
    name?: string;
    status?: ForgeBuildStatus;
    run_mode?: 'build' | 'fetch';
    track_list?: Array<Record<string, unknown>>;
    summary?: Record<string, unknown>;
  }) =>
    request<{ status: string; build: ForgeBuild }>(`/forge/builds/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }).then(r => r.build),
  create: (data: {
    id?: string;
    name?: string;
    source: ForgeBuildSource;
    status?: ForgeBuildStatus;
    run_mode?: 'build' | 'fetch';
    track_list?: Array<Record<string, unknown>>;
    summary?: Record<string, unknown>;
  }) =>
    request<{ status: string; build: ForgeBuild }>('/forge/builds', {
      method: 'POST',
      body: JSON.stringify(data),
    }).then(r => r.build),
  delete: (id: string) =>
    request<{ status: string; deleted: boolean }>(`/forge/builds/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }).then(r => r.deleted),
  publish: (id: string, name?: string) =>
    request<{
      status: string;
      build_id: string;
      platform: string;
      platform_playlist_id: string;
      playlist: { id: string; name: string; track_count: number };
    }>(`/forge/builds/${encodeURIComponent(id)}/publish`, {
      method: 'POST',
      body: JSON.stringify(name ? { name } : {}),
    }),
  fetch: (id: string) =>
    request<{ status: string; message: string }>(`/forge/builds/${encodeURIComponent(id)}/fetch`, {
      method: 'POST',
    }),
};

export const forgeSyncApi = {
  load: (data: { source_url: string; source?: 'spotify' | 'lastfm' | 'deezer'; queue_build?: boolean; name?: string }) =>
    request<{
      status: string;
      source: string;
      name?: string;
      track_count: number;
      owned_count: number;
      missing_count: number;
      queue_build: boolean;
      build?: ForgeBuild | null;
      tracks: Array<{
        track_id?: string;
        spotify_track_id?: string;
        track_name: string;
        artist_name: string;
        album_name: string;
        is_owned: boolean;
      }>;
    }>('/forge/sync/load', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};

export const libraryPlaylistsApi = {
  list: () =>
    request<{ status: string; playlists: LibPlaylist[] }>('/library/playlists')
      .then(r => r.playlists),
  getTracks: (id: string) =>
    request<{ status: string; tracks: LibPlaylistTrack[] }>(
      `/library/playlists/${encodeURIComponent(id)}/tracks`
    ).then(r => r.tracks),
  sync: () =>
    request<{ status: string; playlists_synced: number; tracks_synced: number }>(
      '/library/playlists/sync',
      { method: 'POST' }
    ),
  rename: (id: string, name: string) =>
    request<{ status: string; id: string; name: string }>(
      `/library/playlists/${encodeURIComponent(id)}`,
      { method: 'PATCH', body: JSON.stringify({ name }) }
    ),
  delete: (id: string) =>
    request<{ status: string; id: string }>(
      `/library/playlists/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
  addTracks: (id: string, trackIds: string[]) =>
    request<{ status: string; playlist_id: string; added_count: number; track_count: number }>(
      `/library/playlists/${encodeURIComponent(id)}/tracks`,
      { method: 'POST', body: JSON.stringify({ track_ids: trackIds }) }
    ),
};
