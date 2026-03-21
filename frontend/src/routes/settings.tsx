import { createFileRoute } from '@tanstack/react-router';
import { SettingsPage } from '../pages/Settings';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/settings')({
  component: SettingsRoute,
});

function SettingsRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <SettingsPage toast={toast} />;
}
