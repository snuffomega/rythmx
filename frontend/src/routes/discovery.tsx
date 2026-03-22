import { createFileRoute } from '@tanstack/react-router';
import { Discovery } from '../pages/Discovery';

export const Route = createFileRoute('/discovery')({
  component: Discovery,
});
