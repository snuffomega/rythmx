import type {
  Period,
  AcquisitionStatus,
  QueueItem,
  AcquisitionStats,
  PlaylistItem,
  PlaylistTrack,
  CruiseControlStatus,
  CruiseControlConfig,
  HistoryItem,
  Artist,
  Track,
  TopAlbum,
  LibraryStatus,
  LibraryEnrichStatus,
  SpotifyEnrichStatus,
  LastfmTagsStatus,
  DeezerBpmStatus,
  ConnectionStatus,
  Settings,
  ReleaseKind,
  PersonalDiscoveryConfig,
  PersonalDiscoveryResult,
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

export const cruiseControlApi = {
  getStatus: () =>
    request<{ status: string } & CruiseControlStatus>('/cruise-control/status')
      .then(r => r as CruiseControlStatus),
  getConfig: () =>
    request<{ status: string; config: CruiseControlConfig }>('/cruise-control/config')
      .then(r => r.config),
  saveConfig: (config: Partial<CruiseControlConfig>) =>
    request<{ status: string }>('/cruise-control/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
  runNow: (runMode?: string) =>
    request<{ status: string }>('/cruise-control/run-now', {
      method: 'POST',
      body: runMode ? JSON.stringify({ run_mode: runMode }) : undefined,
    }),
  getHistory: () =>
    request<{ status: string; history: HistoryItem[] }>('/cruise-control/history')
      .then(r => r.history),
};

export const releaseCacheApi = {
  clear: () =>
    request<{ status: string }>('/release-cache/clear', { method: 'POST' }),
};

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
  }) =>
    request<{ status: string }>('/acquisition/queue', {
      method: 'POST',
      body: JSON.stringify(item),
    }),
  getStats: () =>
    request<{ status: string } & AcquisitionStats>('/acquisition/stats')
      .then(({ status: _s, ...stats }) => stats as AcquisitionStats),
  checkNow: () =>
    request<{ status: string }>('/acquisition/check-now', { method: 'POST' }),
};

export const playlistsApi = {
  getAll: () =>
    request<{ status: string; playlists: PlaylistItem[] }>('/playlists')
      .then(r => r.playlists),
  create: (data: Partial<PlaylistItem>) =>
    request<{ status: string } & PlaylistItem>('/playlists', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  update: (name: string, data: Partial<PlaylistItem>) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  rename: (name: string, newName: string) =>
    request<{ status: string; name: string }>(`/playlists/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify({ new_name: newName }),
    }),
  removeTrack: (playlistName: string, rowId: number) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(playlistName)}/tracks/${rowId}`, {
      method: 'DELETE',
    }),
  delete: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  getTracks: (name: string) =>
    request<{ status: string; tracks: PlaylistTrack[] }>(
      `/playlists/${encodeURIComponent(name)}/tracks`
    ).then(r => r.tracks),
  build: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}/build`, {
      method: 'POST',
    }),
  rebuild: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}/rebuild`, {
      method: 'POST',
    }),
  sync: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}/sync`, {
      method: 'POST',
    }),
  publish: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}/publish`, {
      method: 'POST',
    }),
  export: (name: string) =>
    request<{ status: string }>(`/playlists/${encodeURIComponent(name)}/export`, {
      method: 'POST',
    }),
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
  enrichStatus: () =>
    request<{ status: string; enrich_running: boolean } & LibraryStatus>('/library/enrich-status')
      .then(({ status: _s, ...rest }) => rest as LibraryEnrichStatus),
  enrich: (batch_size = 50) =>
    request<{ status: string }>('/library/enrich', {
      method: 'POST',
      body: JSON.stringify({ batch_size }),
    }),
  spotifyStatus: () =>
    request<{ status: string; enrich_running: boolean } & SpotifyEnrichStatus>('/library/spotify-status')
      .then(({ status: _s, ...rest }) => rest as SpotifyEnrichStatus),
  enrichSpotify: (batch_size = 20) =>
    request<{ status: string }>('/library/enrich-spotify', {
      method: 'POST',
      body: JSON.stringify({ batch_size }),
    }),
  lastfmTagsStatus: () =>
    request<{ status: string; enrich_running: boolean } & LastfmTagsStatus>('/library/lastfm-tags-status')
      .then(({ status: _s, ...rest }) => rest as LastfmTagsStatus),
  enrichLastfmTags: (batch_size = 50) =>
    request<{ status: string }>('/library/enrich-lastfm-tags', {
      method: 'POST',
      body: JSON.stringify({ batch_size }),
    }),
  deezerBpmStatus: () =>
    request<{ status: string; enrich_running: boolean } & DeezerBpmStatus>('/library/deezer-bpm-status')
      .then(({ status: _s, ...rest }) => rest as DeezerBpmStatus),
  enrichDeezerBpm: (batch_size = 30) =>
    request<{ status: string }>('/library/enrich-deezer-bpm', {
      method: 'POST',
      body: JSON.stringify({ batch_size }),
    }),
};

export const personalDiscoveryApi = {
  run: (config: PersonalDiscoveryConfig) =>
    request<PersonalDiscoveryResult[]>('/personal-discovery/run', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
};

export const libraryBrowseApi = {
  getArtists: (p: { q?: string; page?: number; per_page?: number; backend?: string; decade?: number | null; region?: string | null } = {}) => {
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
  getTracks: (p: { q?: string; page?: number; per_page?: number } = {}) => {
    const qs = new URLSearchParams(
      Object.entries({ per_page: '100', ...p }).filter(([, v]) => v != null) as [string, string][]
    ).toString();
    return request<{ status: string; tracks: LibTrack[]; total: number; page: number }>(`/library/tracks?${qs}`);
  },
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
