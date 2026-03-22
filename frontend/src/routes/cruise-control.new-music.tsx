import { createFileRoute } from '@tanstack/react-router';
import { CruiseControlNewMusic } from '../pages/CruiseControl';

export const Route = createFileRoute('/cruise-control/new-music')({
  component: CruiseControlNewMusic,
});
