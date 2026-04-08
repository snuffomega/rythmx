import { render, screen } from '@testing-library/react';
import { EnrichmentSection } from '../EnrichmentSection';
import { useEnrichmentStore } from '../../../stores/useEnrichmentStore';

const baseProps = {
  platform: 'plex' as const,
  libraryTrackCount: 1200,
  libraryLastSynced: '2026-04-06',
  auditTotal: 0,
  onOpenAuditReview: vi.fn(),
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
};

describe('EnrichmentSection last run summary', () => {
  beforeEach(() => {
    useEnrichmentStore.getState().reset();
    useEnrichmentStore.getState().setLastRun(null);
    useEnrichmentStore.setState({ running: false, startedAt: null, phase: null });
  });

  it('shows last run line when not running and summary exists', () => {
    useEnrichmentStore.setState({
      running: false,
      lastRun: {
        started_at: '2026-04-06T14:00:00Z',
        ended_at: '2026-04-06T14:14:32Z',
        duration_s: 872,
        outcome: 'completed',
        enriched: 847,
        not_found: 153,
      },
    });
    render(<EnrichmentSection {...baseProps} />);
    expect(screen.getByText(/Last run:/)).toBeInTheDocument();
    expect(screen.getByText(/847 enriched/)).toBeInTheDocument();
  });

  it('hides last run line while running', () => {
    useEnrichmentStore.setState({
      running: true,
      lastRun: {
        started_at: '2026-04-06T14:00:00Z',
        ended_at: '2026-04-06T14:14:32Z',
        duration_s: 872,
        outcome: 'completed',
        enriched: 847,
        not_found: 153,
      },
    });
    render(<EnrichmentSection {...baseProps} />);
    expect(screen.queryByText(/Last run:/)).not.toBeInTheDocument();
  });
});

