interface AudioQualityBadgeProps {
  bit_depth?: number | null;
  sample_rate?: number | null;
  codec?: string | null;
  bitrate?: number | null;
}

/**
 * Renders small pills showing audio quality metadata sourced from lib_tracks.
 * Only Navidrome-synced tracks populate these fields; Plex tracks always
 * receive null and this component renders nothing.
 *
 * Left pill  — bit-depth / sample-rate (from Navidrome sync):
 *   "24-bit / 96kHz"   — when both bit_depth and sample_rate are present
 *   "44.1kHz"          — when only sample_rate is present
 *
 * Right pill — codec + bitrate (from tag_enrichment Stage 1.1):
 *   "FLAC"             — lossless codec with no meaningful bitrate
 *   "MP3 320k"         — lossy codec with bitrate
 *   "AAC 256k"         — etc.
 *
 * Both pills are independent: either may be absent when data is not yet
 * populated or the track has not been enriched.
 */
export function AudioQualityBadge({ bit_depth, sample_rate, codec, bitrate }: AudioQualityBadgeProps) {
  // --- left pill: resolution ---
  let resLabel: string | null = null;
  if (bit_depth && sample_rate) {
    const khz = (sample_rate / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 });
    resLabel = `${bit_depth}-bit / ${khz}kHz`;
  } else if (sample_rate) {
    const khz = (sample_rate / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 });
    resLabel = `${khz}kHz`;
  } else if (bit_depth) {
    resLabel = `${bit_depth}-bit`;
  }

  // --- right pill: codec + bitrate ---
  let codecLabel: string | null = null;
  if (codec) {
    if (bitrate && bitrate > 0) {
      codecLabel = `${codec} ${bitrate}k`;
    } else {
      codecLabel = codec;
    }
  }

  if (!resLabel && !codecLabel) return null;

  const pillClass =
    'inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono leading-none bg-surface-muted text-text-muted whitespace-nowrap select-none';

  return (
    <span className="inline-flex items-center gap-1">
      {resLabel && <span className={pillClass}>{resLabel}</span>}
      {codecLabel && <span className={pillClass}>{codecLabel}</span>}
    </span>
  );
}
