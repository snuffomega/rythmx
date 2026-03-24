import { createFileRoute } from '@tanstack/react-router';
import { ForgeCustomDiscovery } from '../pages/ForgeCustomDiscovery';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/forge/custom-discovery')({
  component: ForgeCustomDiscoveryRoute,
});

function ForgeCustomDiscoveryRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <ForgeCustomDiscovery toast={toast} />;
}
