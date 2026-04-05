import { useState, useEffect, useCallback } from 'react';
import {
  Loader2, Library as LibraryIcon, ChevronRight,
  ListPlus, Play, X, Shuffle, Camera, Check,
} from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { libraryBrowseApi } from '../../services/api';
import { usePlayerStore, type PlayerTrack } from '../../stores/usePlayerStore';
import { useToastStore } from '../../stores/useToastStore';
import { usePlaylistPicker } from '../../hooks/usePlaylistPicker';
import { ApiErrorBanner, AudioQualityBadge, PlaylistPickerModal } from '../common';
import { ArtistImage } from './primitives/ArtistImage';
import { ConfidenceBadge } from './primitives/ConfidenceBadge';
import { SourceChip } from './primitives/SourceChip';
import { AlbumCard } from './cards/AlbumCard';
import { MissingGroupCard } from './cards/MissingGroupCard';
import { MissingKindGroup } from './cards/MissingKindGroup';
import { formatDuration, groupByKind, mergeUniqueTags, formatCount } from './utils';
import type {
  LibArtist,
  LibAlbum,
  LibArtistDetail as LibArtistDetailType,
  MissingAlbum,
  MissingReleaseGroup,
  SimilarArtist,
} from '../../types';

interface ArtistDetailProps {
  artistId: string;
}

export function ArtistDetail({ artistId }: ArtistDetailProps) {
  const toastError = useToastStore(s => s.error);
  const [data, setData] = useState<LibArtistDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showMissing, setShowMissing] = useState(false);
  const [dismissedCount, setDismissedCount] = useState(0);
  const [missingGroups, setMissingGroups] = useState<MissingReleaseGroup[]>([]);
  const [bioExpanded, setBioExpanded] = useState(false);
  const [similar, setSimilar] = useState<SimilarArtist[] | null>(null);
  const [coverEditing, setCoverEditing] = useState(false);
  const [coverInput, setCoverInput] = useState('');
  const [coverSaving, setCoverSaving] = useState(false);
  const [artistImageUrl, setArtistImageUrl] = useState<string | null>(null);
  const picker = usePlaylistPicker();
  const playQueue = usePlayerStore(s => s.playQueue);
  const enqueueNext = usePlayerStore(s => s.enqueueNext);
  const addToQueue = usePlayerStore(s => s.addToQueue);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setSimilar(null);
    setBioExpanded(false);
    libraryBrowseApi.getArtist(artistId)
      .then(res => {
        setData({ artist: res.artist, albums: res.albums, top_tracks: res.top_tracks, missing_albums: res.missing_albums || [] });
        setDismissedCount(res.dismissed_count ?? 0);
        setMissingGroups(res.missing_groups ?? []);
        setArtistImageUrl(res.artist.image_url ?? null);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load artist'))
      .finally(() => setLoading(false));
  }, [artistId]);

  useEffect(() => {
    libraryBrowseApi.getSimilarArtists(artistId)
      .then(res => setSimilar(res.similar ?? []))
      .catch(() => setSimilar([]));
  }, [artistId]);

  const handleDismiss = useCallback((releaseId: string) => {
    libraryBrowseApi.updateReleasePrefs(releaseId, { dismissed: true }).then(() => {
      setData(prev => {
        if (!prev) return prev;
        return { ...prev, missing_albums: (prev.missing_albums || []).filter(r => r.id !== releaseId) };
      });
      // Also filter from groups — remove the edition, remove the group if empty
      setMissingGroups(prev => prev
        .map(g => ({
          ...g,
          editions: g.editions.filter(e => e.id !== releaseId),
          edition_count: g.editions.filter(e => e.id !== releaseId).length,
          primary: g.primary.id === releaseId && g.editions.length > 1
            ? g.editions.find(e => e.id !== releaseId)!
            : g.primary,
        }))
        .filter(g => g.editions.length > 0 && !g.editions.every(e => e.is_owned))
      );
      setDismissedCount(c => c + 1);
    });
  }, []);

  const fetchAllArtistTracks = useCallback(async (artistObj: LibArtist): Promise<PlayerTrack[]> => {
    const res = await libraryBrowseApi.getTracks({ artist_id: artistObj.id, per_page: 500 });
    const tracks = res.tracks.map(t => ({
      id: t.id,
      title: t.title,
      artist: artistObj.name,
      album: t.album_title ?? '',
      duration: t.duration,
      thumb_url: t.thumb_url ?? null,
      thumb_hash: t.thumb_hash ?? null,
      source_platform: artistObj.source_platform ?? 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
    return tracks;
  }, []);

  const handlePlayArtist = useCallback(async () => {
    if (!data) return;
    const tracks = await fetchAllArtistTracks(data.artist);
    if (tracks.length) playQueue(tracks);
  }, [data, fetchAllArtistTracks, playQueue]);

  const handleShuffleArtist = useCallback(async () => {
    if (!data) return;
    const tracks = await fetchAllArtistTracks(data.artist);
    if (!tracks.length) return;
    const shuffled = [...tracks].sort(() => Math.random() - 0.5);
    playQueue(shuffled);
  }, [data, fetchAllArtistTracks, playQueue]);

  const handleQueueArtist = useCallback(async () => {
    if (!data) return;
    const tracks = await fetchAllArtistTracks(data.artist);
    if (!tracks.length) return;
    addToQueue(tracks);
  }, [data, fetchAllArtistTracks, addToQueue]);

  const handlePlayOwnedAlbum = useCallback(async (albumItem: LibAlbum) => {
    try {
      const res = await libraryBrowseApi.getAlbum(albumItem.id);
      const fallbackArtistName = albumItem.artist_name?.trim() || data?.artist.name || '';
      const queueTracks: PlayerTrack[] = res.tracks.map(t => ({
        id: t.id,
        title: t.title,
        artist: fallbackArtistName,
        album: albumItem.title,
        duration: t.duration,
        thumb_url: albumItem.thumb_url ?? null,
        thumb_hash: albumItem.thumb_hash ?? null,
        source_platform: albumItem.source_platform ?? 'navidrome',
        codec: t.codec,
        bitrate: t.bitrate,
        bit_depth: t.bit_depth,
        sample_rate: t.sample_rate,
      }));
      if (!queueTracks.length) {
        toastError(`No playable tracks found for "${albumItem.title}"`);
        return;
      }
      playQueue(queueTracks);
    } catch (err) {
      toastError(err instanceof Error ? err.message : 'Failed to play album');
    }
  }, [data?.artist.name, playQueue, toastError]);

  const handlePlaySimilar = useCallback(async (similarArtist: SimilarArtist) => {
    if (!similarArtist.library_id) return;
    try {
      const res = await libraryBrowseApi.getTracks({ artist_id: similarArtist.library_id, per_page: 500 });
      const tracks: PlayerTrack[] = res.tracks.map(t => ({
        id: t.id,
        title: t.title,
        artist: similarArtist.name,
        album: t.album_title ?? '',
        duration: t.duration,
        thumb_url: t.thumb_url ?? null,
        thumb_hash: t.thumb_hash ?? null,
        source_platform: 'navidrome',
        codec: t.codec,
        bitrate: t.bitrate,
        bit_depth: t.bit_depth,
        sample_rate: t.sample_rate,
      }));
      if (!tracks.length) {
        toastError(`No playable tracks found for "${similarArtist.name}"`);
        return;
      }
      const { currentTrack, queue } = usePlayerStore.getState();
      if (!currentTrack || queue.length === 0) {
        playQueue(tracks);
      } else {
        enqueueNext(tracks);
      }
    } catch (err) {
      toastError(err instanceof Error ? err.message : 'Failed to start similar artist radio');
    }
  }, [enqueueNext, playQueue, toastError]);

  const handleSaveCover = useCallback(async () => {
    if (!coverInput.trim() || !data) return;
    setCoverSaving(true);
    try {
      await libraryBrowseApi.setArtistCover(data.artist.id, coverInput.trim());
      setArtistImageUrl(coverInput.trim());
      setCoverEditing(false);
      setCoverInput('');
    } finally {
      setCoverSaving(false);
    }
  }, [coverInput, data]);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !data) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  const { artist, albums, top_tracks, missing_albums = [] } = data;
  const totalDuration = top_tracks.reduce((s, t) => s + (t.duration ?? 0), 0);
  const deezerPopularityCount = top_tracks.filter(t => t.popularity_source === 'deezer').length;
  const usingDeezerPopularity = deezerPopularityCount > 0;
  const popularityHint = usingDeezerPopularity
    ? `Ranked by Deezer public popularity${deezerPopularityCount < top_tracks.length ? ` (${deezerPopularityCount}/${top_tracks.length} matched in library)` : ''}`
    : 'Sorted by your local play count';
  const tags = mergeUniqueTags(artist.lastfm_tags_json, artist.genres_json).slice(0, 8);
  const listeners = formatCount(artist.listener_count);
  const fans = formatCount(artist.fans_deezer);
  const bioShort = artist.bio_lastfm ? artist.bio_lastfm.slice(0, 220) : null;
  const bioFull = artist.bio_lastfm ?? null;
  const bioTruncated = bioFull && bioFull.length > 220;
  const similarInLib = (similar ?? []).filter(s => s.in_library);
  const similarNotInLib = (similar ?? []).filter(s => !s.in_library);
  const popularQueue: PlayerTrack[] = top_tracks.map(tr => ({
    id: tr.id,
    title: tr.title,
    artist: artist.name,
    album: tr.album_title ?? '',
    duration: tr.duration,
    thumb_url: null,
    thumb_hash: null,
    source_platform: artist.source_platform ?? 'navidrome',
    codec: tr.codec,
    bitrate: tr.bitrate,
    bit_depth: tr.bit_depth,
    sample_rate: tr.sample_rate,
  }));

  function playPopularTrackNow(trackId: string) {
    const idx = popularQueue.findIndex((track) => track.id === trackId);
    if (idx >= 0) {
      playQueue(popularQueue.slice(idx));
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2">
        <Link to="/library" className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        {/* Artist image with cover edit overlay */}
        <div className="relative w-60 h-60 flex-shrink-0 group/cover">
          <div className="w-full h-full bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
            <ArtistImage
              name={artist.name}
              size={56}
              imageUrl={artistImageUrl ?? artist.image_url}
              imageHash={artist.image_hash}
              matchConfidence={artist.match_confidence}
            />
          </div>
          <button
            onClick={() => { setCoverEditing(v => !v); setCoverInput(''); }}
            className="absolute bottom-1.5 right-1.5 bg-black/70 hover:bg-black/90 text-text-muted hover:text-white rounded p-1 opacity-0 group-hover/cover:opacity-100 transition-all"
            aria-label="Edit cover photo"
          >
            <Camera size={13} />
          </button>
        </div>

        <div className="flex flex-col justify-center min-w-0 flex-1">
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{artist.name}</h1>

          {/* Tag chips */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {tags.map(tag => (
                <span key={tag} className="font-mono text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-muted">
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Formation badges */}
          {(artist.formed_year_musicbrainz || artist.area_musicbrainz || artist.begin_area_musicbrainz) && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {artist.formed_year_musicbrainz && (
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-secondary">
                  est. {artist.formed_year_musicbrainz}
                </span>
              )}
              {(artist.begin_area_musicbrainz || artist.area_musicbrainz) && (
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-secondary">
                  {artist.begin_area_musicbrainz ?? artist.area_musicbrainz}
                </span>
              )}
            </div>
          )}

          {/* Popularity signals */}
          {(listeners || fans) && (
            <p className="font-mono text-[10px] text-text-muted mb-2">
              {[listeners && `${listeners} listeners`, fans && `${fans} fans`].filter(Boolean).join(' · ')}
            </p>
          )}

          <div className="flex items-center gap-2 mt-1">
            <ConfidenceBadge value={artist.match_confidence} />
            <SourceChip backend={artist.source_platform} />
            <span className="font-mono text-[10px] text-text-muted">{artist.album_count} albums</span>
          </div>

          {/* Play / Shuffle buttons */}
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={handlePlayArtist}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/80 rounded text-black text-xs font-semibold transition-colors"
              aria-label="Play artist"
            >
              <Play size={12} className="fill-current" /> Play
            </button>
            <button
              onClick={handleShuffleArtist}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-[#333] hover:border-[#555] rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Shuffle artist"
            >
              <Shuffle size={12} /> Shuffle
            </button>
            <button
              onClick={handleQueueArtist}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-[#333] hover:border-[#555] rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Queue artist"
            >
              <ListPlus size={12} /> Queue
            </button>
          </div>

          {/* Cover URL editor */}
          {coverEditing && (
            <div className="flex items-center gap-2 mt-2">
              <input
                type="url"
                value={coverInput}
                onChange={e => setCoverInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleSaveCover(); if (e.key === 'Escape') { setCoverEditing(false); setCoverInput(''); } }}
                placeholder="Paste image URL…"
                className="flex-1 bg-[#111] border border-[#333] rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent font-mono"
                autoFocus
              />
              <button
                onClick={handleSaveCover}
                disabled={coverSaving || !coverInput.trim()}
                className="flex items-center gap-1 px-2 py-1 bg-accent hover:bg-accent/80 disabled:opacity-40 rounded text-black text-xs font-semibold transition-colors"
                aria-label="Save cover"
              >
                {coverSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              </button>
              <button
                onClick={() => { setCoverEditing(false); setCoverInput(''); }}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
                aria-label="Cancel"
              >
                <X size={14} />
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-[#1a1a1a]" />

      {/* Bio */}
      {bioFull && (
        <div className="px-8 py-5">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-2">About</h2>
          <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-line">
            {bioExpanded ? bioFull : (bioTruncated ? `${bioShort}…` : bioFull)}
          </p>
          {bioTruncated && (
            <button
              onClick={() => setBioExpanded(v => !v)}
              className="mt-1.5 font-mono text-[10px] text-accent hover:text-accent/80 transition-colors"
            >
              {bioExpanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      )}

      {/* Similar Artists */}
      {similar !== null && (similarInLib.length > 0 || similarNotInLib.length > 0) && (
        <>
          <div className="border-t border-[#1a1a1a]" />
          <div className="px-8 py-5">
            <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">Similar Artists</h2>

            {similarInLib.length > 0 && (
              <div className="mb-3">
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">In Your Library</p>
                <div className="flex flex-wrap gap-1.5">
                  {similarInLib.map(s => (
                    <div key={s.name} className="flex items-center gap-1">
                      <Link
                        to="/library/artist/$id"
                        params={{ id: s.library_id! }}
                        className="font-mono text-xs px-2 py-1 rounded bg-accent/10 border border-accent/20 text-accent hover:bg-accent/20 transition-colors"
                      >
                        {s.name}
                      </Link>
                      <button
                        onClick={() => handlePlaySimilar(s)}
                        className="p-1 text-text-muted hover:text-accent transition-colors"
                        aria-label={`Play ${s.name} radio`}
                        title="Play similar radio"
                      >
                        <ListPlus size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {similarNotInLib.length > 0 && (
              <div>
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">Also Try</p>
                <div className="flex flex-wrap gap-1.5">
                  {similarNotInLib.map(s => (
                    <span
                      key={s.name}
                      className="font-mono text-xs px-2 py-1 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-muted"
                    >
                      {s.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <div className="border-t border-[#1a1a1a]" />

      {/* Top Tracks */}
      {top_tracks.length > 0 && (
        <div className="px-8 py-5">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-1">
            Popular Tracks
          </h2>
          <p className="font-mono text-[10px] text-text-muted mb-3">{popularityHint}</p>
          <div>
            {top_tracks.map((t, i) => (
              <div
                key={t.id}
                onDoubleClick={() => playPopularTrackNow(t.id)}
                className="group grid grid-cols-[1.75rem_2rem_minmax(0,1.2fr)_minmax(0,1fr)_5.5rem_3.5rem_auto_auto] gap-3 items-center py-2 px-2 hover:bg-[#111] rounded-sm transition-colors"
              >
                <button
                  onClick={() => playPopularTrackNow(t.id)}
                  className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                  aria-label="Play"
                >
                  <Play size={14} />
                </button>
                <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right">{i + 1}</span>
                <span className="text-sm text-text-primary group-hover:text-accent truncate">{t.title}</span>
                <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 truncate">{t.album_title}</span>
                <span
                  className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right"
                  title={t.popularity_source === 'deezer' ? 'Deezer public popularity rank' : 'Local play count fallback'}
                >
                  {t.popularity_source === 'deezer'
                    ? (t.public_popularity ?? 0).toLocaleString()
                    : (t.play_count ?? 0).toLocaleString()}
                </span>
                <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right">{formatDuration(t.duration)}</span>
                <AudioQualityBadge bit_depth={t.bit_depth} sample_rate={t.sample_rate} codec={t.codec} bitrate={t.bitrate} />
                <button
                  onClick={() => picker.openPicker(t)}
                  className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                  title="Add to playlist"
                  aria-label={`Add ${t.title} to playlist`}
                >
                  <ListPlus size={14} />
                </button>
              </div>
            ))}
          </div>
          {totalDuration > 0 && (
            <p className="font-mono text-[10px] text-text-muted mt-2">{Math.round(totalDuration / 60000)} min total</p>
          )}
        </div>
      )}

      <div className="border-t border-[#1a1a1a]" />

      {/* Owned Releases — grouped by kind */}
      <div className="px-8 py-5">
        <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">
          {albums.length} {albums.length === 1 ? 'Release' : 'Releases'}
        </h2>
        {groupByKind(albums, 'record_type').map(group => (
          <div key={group.kind} className="mb-5">
            <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">
              {group.label} ({group.items.length})
            </h3>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
              {group.items.map(al => (
                <AlbumCard
                  key={al.id}
                  album={al}
                  viewMode="grid"
                  onHoverPlay={handlePlayOwnedAlbum}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Missing Releases — collapsed by default */}
      {(missing_albums.length > 0 || missingGroups.length > 0) && (
        <>
          <div className="border-t border-[#1a1a1a]" />
          <div className="px-8 py-5">
            <button
              onClick={() => setShowMissing(!showMissing)}
              className="flex items-center gap-2 text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3 hover:text-text-secondary transition-colors"
            >
              <ChevronRight size={14} className={`transition-transform ${showMissing ? 'rotate-90' : ''}`} />
              {missingGroups.length > 0
                ? `${missingGroups.length} Missing ${missingGroups.length === 1 ? 'Release' : 'Releases'}`
                : `${missing_albums.length} Missing ${missing_albums.length === 1 ? 'Release' : 'Releases'}`
              }
              {dismissedCount > 0 && (
                <span className="text-[9px] font-normal normal-case tracking-normal text-text-muted ml-1">({dismissedCount} dismissed)</span>
              )}
            </button>
            {showMissing && (
              missingGroups.length > 0
                ? groupByKind(missingGroups, 'kind').map(kindGroup => (
                    <div key={kindGroup.kind} className="mb-4 ml-5">
                      <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">
                        {kindGroup.label} ({kindGroup.items.length})
                      </h3>
                      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
                        {kindGroup.items.map(g => (
                          <MissingGroupCard key={g.canonical_release_id} group={g} onDismiss={handleDismiss} />
                        ))}
                      </div>
                    </div>
                  ))
                : groupByKind(missing_albums as (MissingAlbum & { record_type?: string | null })[], 'kind').map(group => (
                    <MissingKindGroup key={group.kind} group={group} onDismiss={handleDismiss} />
                  ))
            )}
          </div>
        </>
      )}

      <PlaylistPickerModal {...picker} />

      <div className="h-4" />
    </div>
  );
}
