import { createFileRoute } from '@tanstack/react-router';
import { PersonalDiscovery } from '../pages/PersonalDiscovery';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/cruise-control/personal-discovery')({
  component: PersonalDiscoveryRoute,
});

function PersonalDiscoveryRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <PersonalDiscovery toast={toast} />;
}
