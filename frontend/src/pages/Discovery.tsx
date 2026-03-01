import { useRef, useState, useEffect } from 'react';
import { Music2, Users, ChevronLeft, ChevronRight, Disc3, Sparkles, Radio } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { useImage } from '../hooks/useImage';
import { statsApi, cruiseControlApi, acquisitionApi } from '../services/api';
import { getImageUrl } from '../utils/imageUrl';
import type { Artist, Track, QueueItem } from '../types';

interface DiscoveryProps {
  onNavigate: (page: string) => void;
}

function placeholderGradient(seed: string) {
  const hues = [200, 160, 220, 180, 30, 270, 340];
  const h = hues[seed.charCodeAt(0) % hues.length];
  return `linear-gradient(135deg, hsl(${h},25%,10%) 0%, hsl(${(h + 40) % 360},20%,16%) 100%)`;
}

function AlbumTile({
  artist,
  title,
  image,
  sub,
  wide,
}: {
  artist: string;
  title: string;
  image?: string;
  sub?: string;
  wide?: boolean;
}) {
  const resolvedImg = useImage('album', title, artist);
  const src = image || resolvedImg;
  const w = wide ? 'w-48' : 'w-40';
  const h = wide ? 'h-48' : 'h-40';
  return (
    <div className={`flex-shrink-0 ${w} snap-start group cursor-pointer`}>
      <div
        className={`${w} ${h} overflow-hidden relative`}
        style={!src ? { background: placeholderGradient(artist) } : undefined}
      >
        {src ? (
          <img
            src={getImageUrl(src)}
            alt={title}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Music2 size={26} className="text-[#333]" />
          </div>
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors duration-300" />
      </div>
      <p className="text-text-primary text-sm font-semibold mt-2 truncate leading-tight">{title}</p>
      <p className="text-[#555] text-xs truncate mt-0.5">{artist}</p>
      {sub && <p className="text-[#3a3a3a] text-xs truncate mt-0.5">{sub}</p>}
    </div>
  );
}

function ArtistTile({ artist }: { artist: Artist }) {
  const resolvedImg = useImage('artist', artist.name);
  const src = artist.image || resolvedImg;
  return (
    <div className="flex-shrink-0 w-36 snap-start text-center group cursor-pointer">
      <div
        className="w-36 h-36 overflow-hidden mx-auto relative"
        style={!src ? { background: placeholderGradient(artist.name) } : undefined}
      >
        {src ? (
          <img
            src={getImageUrl(src)}
            alt={artist.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Users size={24} className="text-[#333]" />
          </div>
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors duration-300" />
      </div>
      <p className="text-text-secondary text-xs font-semibold mt-2 truncate px-1">{artist.name}</p>
      {artist.playcount !== undefined && (
        <p className="text-[#3a3a3a] text-[10px] mt-0.5 tabular-nums">{artist.playcount.toLocaleString()} plays</p>
      )}
    </div>
  );
}

function TrackRow({ track, rank }: { track: Track; rank?: number }) {
  return (
    <div className="flex items-center gap-3 py-3 hover:bg-[#0e0e0e] transition-colors cursor-pointer group border-b border-[#111]">
      {rank !== undefined && (
        <span className="w-5 text-right text-[#333] text-xs tabular-nums flex-shrink-0 font-medium">{rank}</span>
      )}
      <div
        className="w-10 h-10 flex-shrink-0 overflow-hidden"
        style={{ background: placeholderGradient(track.artist) }}
      >
        <div className="w-full h-full flex items-center justify-center">
          <Disc3 size={28} className="text-[#555]" />
        </div>
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-text-primary text-sm font-medium truncate group-hover:text-accent transition-colors">{track.name}</p>
        <p className="text-[#555] text-xs truncate">{track.artist}</p>
      </div>
      {track.playcount !== undefined && (
        <span className="text-[#333] text-xs tabular-nums flex-shrink-0">{track.playcount.toLocaleString()}</span>
      )}
    </div>
  );
}

function SectionHeader({
  icon,
  title,
  sub,
  cta,
  onCta,
}: {
  icon: React.ReactNode;
  title: string;
  sub?: string;
  cta?: string;
  onCta?: () => void;
}) {
  return (
    <div className="flex items-end justify-between mb-5">
      <div>
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-accent">{icon}</span>
          <h2 className="text-text-primary font-bold text-base tracking-tight">{title}</h2>
        </div>
        {sub && <p className="text-[#444] text-xs">{sub}</p>}
      </div>
      {cta && onCta && (
        <button
          onClick={onCta}
          className="flex items-center gap-1 text-[#444] hover:text-text-muted transition-colors text-xs font-medium"
        >
          {cta}
          <ChevronRight size={12} />
        </button>
      )}
    </div>
  );
}

function HScrollShelf({ children }: { children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  const update = () => {
    const el = ref.current;
    if (!el) return;
    setCanLeft(el.scrollLeft > 4);
    setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    update();
    el.addEventListener('scroll', update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => { el.removeEventListener('scroll', update); ro.disconnect(); };
  }, []);

  const scroll = (dir: 'left' | 'right') => {
    ref.current?.scrollBy({ left: dir === 'right' ? 320 : -320, behavior: 'smooth' });
  };

  return (
    <div className="relative">
      {canLeft && (
        <button
          onClick={() => scroll('left')}
          className="hidden lg:flex absolute left-0 top-0 bottom-2 z-10 w-12 items-center justify-start bg-gradient-to-r from-[#0a0a0a] via-[#0a0a0a]/70 to-transparent"
        >
          <ChevronLeft size={20} className="text-white/50 hover:text-white/90 transition-colors" />
        </button>
      )}
      <div
        ref={ref}
        className="flex gap-4 overflow-x-auto scrollbar-hide snap-x pb-2 -mx-1 px-1"
      >
        {children}
      </div>
      {canRight && (
        <button
          onClick={() => scroll('right')}
          className="hidden lg:flex absolute right-0 top-0 bottom-2 z-10 w-12 items-center justify-end bg-gradient-to-l from-[#0a0a0a] via-[#0a0a0a]/70 to-transparent"
        >
          <ChevronRight size={20} className="text-white/50 hover:text-white/90 transition-colors" />
        </button>
      )}
    </div>
  );
}

function ShelfSkeleton({ count = 6, size = 160 }: { count?: number; size?: number }) {
  return (
    <div className="flex gap-4 overflow-x-auto scrollbar-hide pb-2">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex-shrink-0 space-y-2" style={{ width: size }}>
          <div className="animate-pulse bg-[#141414]" style={{ width: size, height: size }} />
          <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-3/4" />
          <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-1/2" />
        </div>
      ))}
    </div>
  );
}

function TrackListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 py-3 border-b border-[#111]">
          <div className="w-10 h-10 animate-pulse bg-[#141414] flex-shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-40" />
            <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-28" />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyShelf({ message }: { message: string }) {
  return <p className="text-[#333] text-sm py-6">{message}</p>;
}

export function Discovery({ onNavigate }: DiscoveryProps) {
  const topArtists = useApi(() => statsApi.getTopArtists('1month', 12));
  const lovedArtists = useApi(() => statsApi.getLovedArtists());
  const topTracks = useApi(() => statsApi.getTopTracks('7day', 20));
  const recentQueue = useApi(() => acquisitionApi.getQueue('pending'));
  const history = useApi(() => cruiseControlApi.getHistory());

  const newReleases = history.data?.length ? history.data.slice(0, 14) : null;

  return (
    <div className="py-8 space-y-12">

      <div>
        <h1 className="page-title mb-1">For You</h1>
        <p className="text-[#444] text-sm">Your music world, at a glance</p>
      </div>

      <section>
        <SectionHeader
          icon={<Disc3 size={15} />}
          title="New Releases"
          sub="From your last Cruise Control run"
          cta="View all"
          onCta={() => onNavigate('cruise-control')}
        />
        {history.loading ? (
          <ShelfSkeleton count={7} size={160} />
        ) : !newReleases || newReleases.length === 0 ? (
          <EmptyShelf message="Run Cruise Control to populate new releases" />
        ) : (
          <HScrollShelf>
            {newReleases.map((item, i) => (
              <AlbumTile key={i} artist={item.artist} title={item.album} sub={new Date(item.date).toLocaleDateString()} />
            ))}
          </HScrollShelf>
        )}
      </section>

      <section>
        <SectionHeader
          icon={<Radio size={15} />}
          title="Trending for You"
          sub="Your most-played tracks this week"
          cta="See stats"
          onCta={() => onNavigate('stats')}
        />
        {topTracks.loading ? (
          <TrackListSkeleton count={8} />
        ) : !topTracks.data || topTracks.data.length === 0 ? (
          <EmptyShelf message="Connect Last.fm in Settings to see trending tracks" />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8">
            {topTracks.data.slice(0, 16).map((track, i) => (
              <TrackRow key={i} track={track} rank={i + 1} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionHeader
          icon={<Sparkles size={15} />}
          title="Discover"
          sub="Artists similar to your taste you haven't heard much"
          cta="Run discovery"
          onCta={() => onNavigate('cruise-control')}
        />
        {lovedArtists.loading ? (
          <ShelfSkeleton count={6} size={144} />
        ) : !lovedArtists.data || lovedArtists.data.length === 0 ? (
          <EmptyShelf message="Love tracks on Last.fm or run Personal Discovery to see suggestions" />
        ) : (
          <HScrollShelf>
            {lovedArtists.data.map((artist, i) => (
              <ArtistTile key={i} artist={artist} />
            ))}
          </HScrollShelf>
        )}
      </section>

      <section>
        <SectionHeader
          icon={<Users size={15} />}
          title="From Your Library"
          sub="Artists you've been spinning lately"
          cta="View all"
          onCta={() => onNavigate('stats')}
        />
        {topArtists.loading ? (
          <ShelfSkeleton count={6} size={144} />
        ) : !topArtists.data || topArtists.data.length === 0 ? (
          <EmptyShelf message="Connect Last.fm in Settings to see your top artists" />
        ) : (
          <HScrollShelf>
            {topArtists.data.map((artist, i) => (
              <ArtistTile key={i} artist={artist} />
            ))}
          </HScrollShelf>
        )}
      </section>

      {recentQueue.data && recentQueue.data.length > 0 && (
        <section>
          <SectionHeader
            icon={<Music2 size={15} />}
            title="Queued for Acquisition"
            sub="Albums being tracked"
            cta="View activity"
            onCta={() => onNavigate('activity')}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {recentQueue.data.slice(0, 8).map((item: QueueItem, i) => (
              <div
                key={i}
                className="flex items-center gap-3 p-3 bg-[#0d0d0d] border border-[#141414] hover:border-[#222] transition-colors cursor-pointer group"
              >
                <div
                  className="w-10 h-10 flex-shrink-0"
                  style={{ background: placeholderGradient(item.artist) }}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-text-primary text-xs font-semibold truncate">{item.album}</p>
                  <p className="text-[#444] text-xs truncate">{item.artist}</p>
                </div>
                <span className="badge-muted flex-shrink-0 text-[10px]">{item.status}</span>
              </div>
            ))}
          </div>
        </section>
      )}

    </div>
  );
}
