// All backend responses follow { status: 'ok', ...payload } or { status: 'error', error: string }.
// The request() function in api.ts throws on status='error', so callers always receive the ok shape.
export type ApiOkResponse = { status: 'ok' };
export type ApiErrorResponse = { status: 'error'; error?: string; message?: string };

export type Period = '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
export type AcquisitionStatus = 'pending' | 'submitted' | 'found' | 'failed' | 'skipped';
export type ReleaseKind = 'album' | 'ep' | 'single' | 'compilation';
export type LibraryPlatform = 'plex' | 'jellyfin' | 'navidrome';

export interface Artist {
  name: string;
  image?: string;
  playcount?: number;
  url?: string;
}

export interface TopAlbum {
  artist: string;
  title: string;
  image?: string;
  playcount?: number;
}

export interface Track {
  name: string;
  artist: string;
  album?: string;
  image?: string;
  playcount?: number;
  duration?: number;
  is_owned?: boolean;
  score?: number;
  rank?: number;
}

export interface QueueItem {
  id: string | number;
  artist: string;
  album: string;
  kind: ReleaseKind;
  status: AcquisitionStatus;
  requested_by?: string;
  requested_at?: string;
  release_date?: string;
}

export interface AcquisitionStats {
  pending: number;
  submitted: number;
  found: number;
  failed: number;
  skipped: number;
  total: number;
}

export type ImageType = 'artist' | 'album' | 'track';

export interface ImageResolveResponse {
  image_url?: string;
  pending?: boolean;
}

export interface CollageItem {
  name: string;
  artist?: string;
  image?: string;
  playcount?: number;
}

export type PlaylistSource = 'taste' | 'lastfm' | 'spotify' | 'deezer' | 'empty' | 'new_music' | 'forge_new_music';

export interface PlaylistItem {
  name: string;
  source?: PlaylistSource;
  last_synced?: string;
  created_at?: string;
  mode?: string;
  auto_sync?: boolean;
  track_count?: number;
  owned_count?: number;
  max_tracks?: number;
  source_url?: string;
}

export interface PlaylistTrack {
  row_id?: number;
  name: string;
  artist: string;
  album?: string;
  image?: string;
  is_owned: boolean;
  score?: number;
  acquisition_status?: AcquisitionStatus | null;
}

export interface HistoryItem {
  artist: string;
  album: string;
  status: 'owned' | 'queued' | 'skipped';
  reason?: string;
  date: string;
}

export type ForgeDiscoveryRunMode = 'build' | 'fetch';

export interface ForgeDiscoveryConfig {
  closeness: number;
  seed_period: '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
  min_scrobbles: number;
  max_tracks: number;
  run_mode?: ForgeDiscoveryRunMode;
  auto_publish?: boolean;
  schedule_enabled?: boolean;
  schedule_weekday?: number;
  schedule_hour?: number;
  dry_run?: boolean;
}

export interface ForgeDiscoveryResult {
  artist: string;
  image?: string;
  reason?: string;
  similarity?: number | null;
  tags?: string[];
}

// Backward-compatible aliases while old UI modules are being renamed.
export type PersonalDiscoveryConfig = ForgeDiscoveryConfig;
export type PersonalDiscoveryResult = ForgeDiscoveryResult;

export interface LibraryStatus {
  platform: LibraryPlatform;
  track_count: number;
  last_synced?: string;
  synced?: boolean;
  total_albums?: number;
  enriched_albums?: number;
  enrich_pct?: number;
}

export interface ConnectionStatus {
  connected: boolean;
  message?: string;
}

export interface Settings {
  lastfm_username?: string;
  lastfm_api_key?: string;
  lastfm_configured?: boolean;
  plex_url?: string;
  plex_token?: string;
  plex_configured?: boolean;
  navidrome_configured?: boolean;
  soulsync_url?: string;
  soulsync_db_accessible?: boolean;
  spotify_client_id?: string;
  spotify_client_secret?: string;
  spotify_configured?: boolean;
  fanart_configured?: boolean;
  library_platform: LibraryPlatform;
  fetch_enabled?: boolean;
}

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

// ---------------------------------------------------------------------------
// Library browse — lib_* table shapes
// ---------------------------------------------------------------------------

export interface LibArtist {
  id: string;
  name: string;
  sort_name: string;
  album_count: number;
  missing_count: number;
  match_confidence: number;
  source_platform: string | null;
  lastfm_tags_json: string | null;
  genres_json: string | null;
  popularity: number | null;
  listener_count: number | null;
  global_play_count: number | null;
  image_url: string | null;
  bio_lastfm: string | null;
  fans_deezer: number | null;
  similar_artists_json: string | null;
  area_musicbrainz: string | null;
  begin_area_musicbrainz: string | null;
  formed_year_musicbrainz: number | null;
}

export interface SimilarArtist {
  name: string;
  in_library: boolean;
  library_id?: string;
}

export interface LibAlbum {
  id: string;
  artist_id: string;
  artist_name: string;
  title: string;
  year: number | null;
  record_type: string | null;
  match_confidence: number;
  needs_verification: number;
  source_platform: string | null;
  release_date: string | null;
  genre: string | null;
  thumb_url: string | null;
  lastfm_tags_json: string | null;
}

export interface LibTrack {
  id: string;
  album_id: string;
  artist_id: string;
  title: string;
  track_number: number | null;
  disc_number: number | null;
  duration: number | null;
  rating: number;
  play_count: number;
  tempo: number | null;
  sample_rate?: number | null;
  bit_depth?: number | null;
  channel_count?: number | null;
  replay_gain_track?: number | null;
  bitrate?: number | null;
  codec?: string | null;
  container?: string | null;
  embedded_lyrics?: string | null;
  tag_genre?: string | null;
  album_title?: string;
  artist_name?: string;
}

export interface MissingAlbum {
  id?: string;
  album_title: string;
  display_title?: string;
  source: string;
  record_type?: string | null;
  album_id?: string;
  kind?: string;
  version_type?: string;
  release_date?: string;
  deezer_album_id?: string;
  itunes_album_id?: string;
  thumb_url?: string;
  track_count?: number;
}

export interface MissingReleaseGroup {
  canonical_release_id: string;
  primary: MissingAlbum & { is_owned?: number };
  edition_count: number;
  owned_count: number;
  editions: (MissingAlbum & { is_owned?: number })[];
  kind: string;
}

export interface LibArtistDetail {
  artist: LibArtist;
  albums: LibAlbum[];
  top_tracks: LibTrack[];
  missing_albums?: MissingAlbum[];
  missing_groups?: MissingReleaseGroup[];
  dismissed_count?: number;
}

export interface UserReleasePrefs {
  release_id: string;
  dismissed: number;
  priority: number;
  notes: string | null;
  updated_at: string;
  source: string;
}

export interface LibAlbumDetail {
  album: LibAlbum;
  tracks: LibTrack[];
}

export interface ReleaseDetail {
  id: string;
  artist_id: string;
  artist_name: string;
  title: string;
  release_date: string | null;
  kind: string;
  version_type: string | null;
  track_count: number | null;
  thumb_url: string | null;
  catalog_source: string | null;
  deezer_album_id: string | null;
  itunes_album_id: string | null;
  explicit: number;
  label: string | null;
  genre_itunes: string | null;
  canonical_release_id: string | null;
  upc_deezer: string | null;
}

export interface ReleaseSibling {
  id: string;
  title: string;
  version_type: string | null;
  release_date: string | null;
  thumb_url: string | null;
  is_owned: number;
  kind: string;
}

export interface ReleaseTrack {
  title: string;
  track_number: number;
  disc_number: number;
  duration_ms: number;
  preview_url: string;
}

// ---------------------------------------------------------------------------
// Library Audit
// ---------------------------------------------------------------------------

export interface AuditEnrichmentMeta {
  status: 'found' | 'not_found' | 'error' | 'fallback' | 'pending';
  confidence: number | null;
}

export interface AuditItem {
  artist_id: string;
  artist_name: string;
  album_id: string;
  album_title: string;
  match_confidence: number | null;
  needs_verification: boolean;
  itunes_album_id: string | null;
  deezer_id: string | null;
  enrichment: Record<string, AuditEnrichmentMeta>;
}

export interface AuditResponse {
  status: 'ok';
  items: AuditItem[];
  total: number;
  page: number;
}

// ---------------------------------------------------------------------------
// Enrichment pipeline — REST status shapes
// ---------------------------------------------------------------------------

export interface EnrichmentWorkerStatus {
  found: number;
  not_found: number;
  errors: number;
  pending: number;
}

export interface EnrichmentPipelineStatus {
  running: boolean;
  // started_at: ISO 8601 UTC string set when run_full() is called; null when not running.
  started_at?: string | null;
  // Current pipeline phase (sync, id_itunes_deezer, id_parallel, etc.). Null when idle.
  phase?: string | null;
  // Per-source worker stats from enrichment_meta. During live runs the
  // id_itunes_deezer worker broadcasts combined progress as "library";
  // REST and completion payloads return individual sub-sources.
  workers: Partial<Record<
    'library' | 'itunes_artist' | 'deezer_artist' | 'itunes' | 'deezer' |
    'itunes_rich' | 'deezer_rich' | 'spotify_id' | 'spotify_genres' |
    'lastfm_id' | 'lastfm_tags' | 'lastfm_stats' | 'artist_art',
    EnrichmentWorkerStatus
  >>;
}

export interface EnrichmentStopResponse {
  status: 'ok' | 'error';
  message: string;
}

// ---------------------------------------------------------------------------
// WebSocket — SHRTA-framed event shapes (Section 6 registry)
// ---------------------------------------------------------------------------

export interface WsEnvelope<T = unknown> {
  event: string;
  payload: T;
  timestamp: number;
}

// Enrichment pipeline events (EnrichmentOrchestrator)
export interface WsEnrichmentProgress {
  worker: string;
  found: number;
  not_found: number;
  errors: number;
  pending: number;
  running: boolean;
}

export interface WsEnrichmentComplete {
  workers: Record<string, WsEnrichmentProgress>;
}

export interface WsEnrichmentStopped {
  message: string;
}

export interface WsEnrichmentPhase {
  phase: string;
}

// New Music pipeline events (scheduler)
export interface WsPipelineProgress {
  stage: string;
  processed: number;
  total: number;
  message: string;
}

// Library sync events
export interface WsLibrarySyncProgress {
  artists: number;
  albums: number;
  tracks: number;
  message: string;
}

// Connection verification
export interface ConnectionServiceStatus {
  service: string;
  display_name: string;
  required: boolean;
  status: string;
  verified_at: string | null;
  message?: string;
  server_name?: string;
  username?: string;
}

export interface ConnectionVerifyResult {
  status: 'ok' | 'partial' | 'error';
  services: Record<string, ConnectionServiceStatus>;
  pipeline_ready: boolean;
}

export interface ConnectionStatusResult {
  services: Record<string, ConnectionServiceStatus>;
  pipeline_ready: boolean;
}

// ---------------------------------------------------------------------------
// The Forge — New Music pipeline
// ---------------------------------------------------------------------------

export interface NewMusicConfig {
  nm_min_scrobbles: number;
  nm_period: '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
  nm_lookback_days: number;
  nm_match_mode: 'strict' | 'loose';
  nm_ignore_keywords: string;
  nm_ignore_artists: string;
  nm_release_kinds: 'all' | 'album_preferred' | 'album';
  nm_schedule_enabled: boolean;
  nm_schedule_weekday: number;
  nm_schedule_hour: number;
}

export interface DiscoveredRelease {
  id: string;
  artist_deezer_id: string;
  artist_name: string;
  title: string;
  record_type: string | null;
  release_date: string | null;
  cover_url: string | null;
  in_library: boolean;
}

// ---------------------------------------------------------------------------
// The Forge - Builds and pipeline history
// ---------------------------------------------------------------------------

export type ForgeBuildSource = 'new_music' | 'custom_discovery' | 'sync' | 'manual';
export type ForgeBuildStatus = 'queued' | 'building' | 'ready' | 'published' | 'failed';

export interface ForgeBuild {
  id: string;
  name: string;
  source: ForgeBuildSource;
  status: ForgeBuildStatus;
  run_mode?: 'build' | 'fetch' | null;
  track_list: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface PipelineRun {
  id: number;
  pipeline_type: 'new_music' | 'custom_discovery';
  run_mode: 'preview' | 'build' | 'fetch';
  status: 'running' | 'completed' | 'error';
  config_json: string;
  started_at: string;
  finished_at: string | null;
  run_duration: number | null;
  summary_json: string | null;
  error_message: string | null;
  triggered_by: 'manual' | 'schedule';
}

// ---------------------------------------------------------------------------
// Library Playlists — lib_playlists + lib_playlist_tracks
// ---------------------------------------------------------------------------

export interface LibPlaylist {
  id: string;
  name: string;
  source_platform: string;
  cover_url: string | null;
  track_count: number;
  duration_ms: number;
  updated_at: string | null;
  synced_at: string | null;
}

export interface LibPlaylistTrack {
  position: number;
  track_id: string;
  title: string;
  artist_name: string | null;
  album_title: string | null;
  duration: number | null;
  file_path: string | null;
}
