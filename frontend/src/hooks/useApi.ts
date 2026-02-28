import { useState, useEffect, useCallback, useRef } from 'react';

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

// key: optional caller-supplied value (e.g. a period string) that re-triggers the effect
// when it changes. Existing callers with no key are unaffected.
export function useApi<T>(fetcher: () => Promise<T>, key?: unknown): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => {
    setTick(t => t + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetcherRef.current()
      .then(result => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'An error occurred');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [tick, key]); // key re-triggers when caller's dependency changes (e.g. period filter)

  return { data, loading, error, refetch };
}
