import { useState } from 'react';
import { Star } from 'lucide-react';

interface StarRatingProps {
  value: number;      // 0-10 stored
  onChange?: (v: number) => void;
  size?: number;
  readonly?: boolean;
}

export function StarRating({ value, onChange, size = 14, readonly = false }: StarRatingProps) {
  const display = Math.round(value / 2);  // 0-10 → 0-5
  const [hover, setHover] = useState<number | null>(null);
  const active = hover ?? display;

  return (
    <div className="flex items-center gap-0.5" onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map(star => (
        <button
          key={star}
          type="button"
          disabled={readonly}
          onMouseEnter={() => !readonly && setHover(star)}
          onClick={() => !readonly && onChange?.(star * 2)}
          className={`transition-colors ${readonly ? 'cursor-default' : 'cursor-pointer'}`}
        >
          <Star
            size={size}
            className={star <= active ? 'text-accent' : 'text-text-muted'}
            fill={star <= active ? 'currentColor' : 'none'}
          />
        </button>
      ))}
    </div>
  );
}
