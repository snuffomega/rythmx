import { createFileRoute } from '@tanstack/react-router';
import { ForgeSync } from '../pages/ForgeSync';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/forge/sync')({
  component: ForgeSyncRoute,
});

function ForgeSyncRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <ForgeSync toast={toast} />;
}
