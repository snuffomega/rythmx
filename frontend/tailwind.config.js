/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        base: 'var(--bg-base)',
        surface: {
          DEFAULT: 'var(--surface)',
          highlight: 'var(--surface-highlight)',
          sunken: 'var(--surface-sunken)',
          skeleton: 'var(--surface-skeleton)',
          raised: 'var(--surface-raised)',
          overlay: 'var(--surface-overlay)',
        },
        border: {
          DEFAULT: 'var(--border-default)',
          subtle: 'var(--border-subtle)',
          input: 'var(--border-input)',
          strong: 'var(--border-strong)',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
          dim: 'var(--text-dim)',
          faint: 'var(--text-faint)',
          soft: 'var(--text-soft)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          muted: 'rgba(212,245,60,0.15)',
        },
        brand: {
          spotify: 'var(--brand-spotify)',
          plex: 'var(--brand-plex)',
          lastfm: 'var(--brand-lastfm)',
        },
        success: {
          DEFAULT: 'var(--accent-success)',
          light: 'var(--success-light)',
        },
        danger: {
          DEFAULT: 'var(--accent-danger)',
          light: 'var(--danger-light)',
        },
        warning: 'var(--accent-warning)',
        'warning-text': 'var(--warning-text)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        display: ['Inter', 'system-ui', 'sans-serif'],
      },
      letterSpacing: {
        tighter: '-0.04em',
        tight: '-0.02em',
      },
      borderRadius: {
        sm: '0.125rem',
        DEFAULT: '0.25rem',
        md: '0.375rem',
        lg: '0.5rem',
        xl: '0.75rem',
        '2xl': '1rem',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.6)',
        glow: '0 0 20px rgba(212,245,60,0.15)',
      },
    },
  },
  plugins: [],
};
