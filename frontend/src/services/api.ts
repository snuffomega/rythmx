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
  StatsSummary,
  LibraryStatus,
  ConnectionStatus,
  Settings,
  ReleaseKind,
  PersonalDiscoveryConfig,
  PersonalDiscoveryResult,
} from '../types';

const BASE_URL = '/api';

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
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
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
  getSummary: () =>
    request<{ status: string; summary: StatsSummary }>('/stats/summary')
      .then(r => r.summary),
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
  setLibraryBackend: (backend: 'soulsync' | 'plex' | 'jellyfin' | 'navidrome') =>
    request<{ status: string }>('/settings/library-backend', {
      method: 'POST',
      body: JSON.stringify({ backend }),
    }),
  clearHistory: () =>
    request<{ status: string }>('/settings/clear-history', { method: 'POST' }),
  resetDb: () =>
    request<{ status: string }>('/settings/reset-db', { method: 'POST' }),
  clearImageCache: () =>
    request<{ status: string }>('/settings/clear-image-cache', { method: 'POST' }),
};

export const libraryApi = {
  getStatus: () =>
    request<{ status: string } & LibraryStatus>('/library/status')
      .then(({ status: _s, ...rest }) => rest as LibraryStatus),
  sync: () =>
    request<{ status: string }>('/library/sync', { method: 'POST' }),
};

export const personalDiscoveryApi = {
  run: (config: PersonalDiscoveryConfig) =>
    request<PersonalDiscoveryResult[]>('/personal-discovery/run', {
      method: 'POST',
      body: JSON.stringify(config),
    }),
};

export const imageServiceApi = {
  warmCache: (maxItems = 40) =>
    request<{ status: string; submitted: number }>('/images/warm-cache', {
      method: 'POST',
      body: JSON.stringify({ max_items: maxItems }),
    }),
};
