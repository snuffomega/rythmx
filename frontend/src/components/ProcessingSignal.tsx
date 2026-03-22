import React from 'react';

interface ProcessingSignalProps {
  isActive: boolean;
  onClick?: () => void;
}

/**
 * ProcessingSignal - The app's "heartbeat" indicator
 *
 * A custom record icon:
 * - Outer ring: Full circle when idle, animated orbiting arc when active
 * - Inner arcs: Two decorative grooves at 2 & 7 o'clock (record look)
 *
 * Like Gemini's spinner but the outer circle stays visible when resting.
 * Click navigates to Settings pipeline section.
 */
const ProcessingSignal: React.FC<ProcessingSignalProps> = ({ isActive, onClick }) => {
  return (
    <button
      data-testid="processing-signal"
      onClick={onClick}
      className="relative flex items-center justify-center"
      title={isActive ? 'Enrichment in progress — Click to view details' : 'Rythmx'}
      aria-label={isActive ? 'Processing active' : 'Processing idle'}
    >
      <svg
        viewBox="0 0 24 24"
        fill="none"
        style={{ width: '30px', height: '30px' }}
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* OUTER RING — animated when active */}
        {isActive ? (
          <circle
            cx="12"
            cy="12"
            r="10.5"
            stroke="currentColor"
            strokeWidth="2"
            fill="none"
            strokeLinecap="round"
            className="text-accent animate-orbit"
            style={{
              strokeDasharray: '22 44',
              transformOrigin: 'center',
            }}
          />
        ) : (
          <circle
            cx="12"
            cy="12"
            r="10.5"
            stroke="currentColor"
            strokeWidth="2"
            fill="none"
            className="text-accent"
          />
        )}

        {/* INNER DECORATIVE ARCS — two grooves at 2 & 7 o'clock */}
        <circle
          cx="12"
          cy="12"
          r="6.5"
          stroke="currentColor"
          strokeWidth="1.8"
          fill="none"
          strokeLinecap="round"
          className="text-accent"
          style={{
            strokeDasharray: '11.4 9 11.4 9',
            transform: 'rotate(-70deg)',
            transformOrigin: 'center',
          }}
        />

        {/* CENTER DOT */}
        <circle
          cx="12"
          cy="12"
          r="2.5"
          fill="currentColor"
          className="text-accent"
        />
      </svg>
    </button>
  );
};

export default ProcessingSignal;
