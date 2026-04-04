# rythmx-asset-orchestration

## Purpose

Defines the image loading strategy, URL resolution chain, proactive cache warming, and the `getImageUrl()` abstraction for album art in Rythmx. All image loading must go through this system — never hardcode image URLs directly in components.

---

## 1. The `getImageUrl()` Abstraction

All album art URL construction uses `getImageUrl()` from `frontend/src/utils/imageUrl.ts`. Never build `/api/v1/artwork/` URLs manually in components.

**Signature:**
```typescript
// frontend/src/utils/imageUrl.ts
export function getImageUrl(
  rawUrl: string | undefined | null,
  contentHash?: string | null,
  size = 300
): string
```

**Resolution logic:**
- If `rawUrl` already starts with `/api/v1/artwork/` → return as-is
- If `contentHash` is present → return `/api/v1/artwork/{hash}?s={size}`
- Otherwise → return `rawUrl` (CDN fallback)

**Never do this in components:**
```typescript
// WRONG — manual URL construction
<img src={`/api/v1/artwork/${hash}?s=300`} />

// CORRECT
<img src={getImageUrl(thumbUrl, thumbHash)} />
```

---

## 2. Enqueue-Time Hydration (`hydrateTrackArtwork`)

All queue-building paths must call `hydrateTrackArtwork` before pushing tracks to the player store. This is the single point of responsibility for artwork on `PlayerTrack` objects.

**Function:** `hydrateTrackArtwork(track: PlayerTrack): Promise<PlayerTrack>`  
**Location:** `frontend/src/services/api.ts`

```typescript
// Pattern for all queue-building paths:
const hydrated = await Promise.all(tracks.map(hydrateTrackArtwork));
playQueue(hydrated);
```

- No-ops immediately if `track.thumb_hash` is already set
- Calls `POST /api/v1/images/resolve-batch` for the track's album/artist
- On success with `content_hash` → sets `thumb_hash` + `thumb_url` on the returned track
- On `pending: true` or error → returns track as-is (player falls back to `useImage` hook)

---

## 3. Batch Image Resolution API

**Endpoint:** `POST /api/v1/images/resolve-batch`

**Request:**
```json
{
  "items": [
    { "id": "any-string", "type": "album", "name": "OK Computer", "artist": "Radiohead" }
  ]
}
```

**Response:**
```json
{
  "items": [
    { "id": "any-string", "image_url": "https://...", "content_hash": "abc123...", "pending": false }
  ]
}
```

- `content_hash` is a SHA-256 hex string — use it with `getImageUrl(imageUrl, contentHash)` to build a stable local URL
- `pending: true` means the background fetch is in progress — retry after 3–6 s

---

## 4. Local Artwork Serving

**Endpoint:** `GET /api/v1/artwork/{content_hash}?s={size}`

Serves a WebP thumbnail from local content-addressed storage. Generates on miss from the original blob. Size is clamped to 32–2048px (default 300).

Response headers: `Cache-Control: public, max-age=31536000, immutable`

---

## 5. Backend Resolution Chain

`app/services/image_service.py` resolves image URLs in priority order:

**For artists:**
1. Navidrome `coverArt` (if platform=navidrome)
2. Fanart.tv (real band photo via MBID, requires `FANART_API_KEY`)
3. Deezer artist photo
4. iTunes album art last resort

**For albums:**
1. iTunes direct lookup (if `itunes_album_id` known)
2. iTunes name search
3. iTunes search with punctuation-stripped artist
4. Deezer album search

After a URL is resolved, the L3 worker (`_fetch_and_cache`) **must** download the bytes and call `artwork_store.ingest()` to produce a local `content_hash`. URL-only cache entries are not the final state.

---

## 6. `useImage` Hook (Frontend Resolver Fallback)

Used in components when no `thumb_hash` is available on the track data.

**Location:** `frontend/src/hooks/useImage.ts`

```typescript
const resolved = useImage('album', title, artist);
const src = thumbUrl
  ? getImageUrl(thumbUrl, thumbHash ?? null)
  : (resolved ? getImageUrl(resolved) : null);
```

Batches up to N concurrent component requests into a single `resolve-batch` call every 25 ms. Applies exponential backoff (3 s, 6 s, 12 s, 24 s) on `pending` responses.

---

## 7. Shared `TrackArt` Component

**Location:** `frontend/src/components/common/TrackArt.tsx`

Use for all player artwork rendering. Wraps `useImage` + `getImageUrl` logic.

```typescript
<TrackArt
  thumbUrl={track.thumb_url}
  thumbHash={track.thumb_hash}
  title={track.album}
  artist={track.artist}
  size="sm"   // 'sm' | 'lg' | 'fill'
/>
```

---

## Asset Orchestration Checklist

- [ ] All queue-building paths call `hydrateTrackArtwork()` before pushing to store
- [ ] L3 `_fetch_and_cache` calls `artwork_store.ingest()` on URL resolution success
- [ ] All image URLs constructed via `getImageUrl()` from `utils/imageUrl.ts`
- [ ] No hardcoded `/api/v1/artwork/...` URL strings in components
- [ ] Resolution chain order maintained: Fanart.tv → Deezer → iTunes
- [ ] `useImage` hook used in components for resolver fallback
- [ ] `None`/`null` URL returns gracefully (component shows placeholder, not broken image)
- [ ] Shared `TrackArt` component used for player artwork (no local duplicates)
