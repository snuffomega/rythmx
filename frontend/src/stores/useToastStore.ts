import { create } from 'zustand';
import type { Toast } from '../types';

interface ToastStore {
  toasts: Toast[];
  success: (message: string) => void;
  error: (message: string) => void;
  warn: (message: string) => void;
  info: (message: string) => void;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => {
  const add = (type: Toast['type'], message: string) => {
    const id = Math.random().toString(36).slice(2);
    set(s => ({ toasts: [...s.toasts, { id, type, message }] }));
    setTimeout(() => {
      set(s => ({ toasts: s.toasts.filter(t => t.id !== id) }));
    }, 4000);
  };

  return {
    toasts: [],
    success: (message: string) => add('success', message),
    error: (message: string) => add('error', message),
    warn: (message: string) => add('warning', message),
    info: (message: string) => add('info', message),
    dismiss: (id: string) => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
  };
});
