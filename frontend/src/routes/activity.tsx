import { createFileRoute } from '@tanstack/react-router';
import { ActivityPage } from '../pages/Activity';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/activity')({
  component: ActivityRoute,
});

function ActivityRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <ActivityPage toast={toast} />;
}
