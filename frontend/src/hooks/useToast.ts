import { useState, useCallback } from 'react';
import type { Toast } from '../types';

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const add = useCallback((type: Toast['type'], message: string) => {
    const id = Math.random().toString(36).slice(2);
    setToasts(prev => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  }, []);

  const success = useCallback((message: string) => add('success', message), [add]);
  const error = useCallback((message: string) => add('error', message), [add]);
  const warn = useCallback((message: string) => add('warning', message), [add]);
  const info = useCallback((message: string) => add('info', message), [add]);

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return { toasts, success, error, warn, info, dismiss };
}
