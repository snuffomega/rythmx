/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        base: '#0A0A0A',
        surface: {
          DEFAULT: '#111111',
          highlight: '#181818',
        },
        border: {
          DEFAULT: '#222222',
        },
        text: {
          primary: '#F0F0F0',
          secondary: '#999999',
          muted: '#555555',
        },
        accent: {
          DEFAULT: '#D4F53C',
          hover: '#C2E030',
          muted: 'rgba(212,245,60,0.15)',
        },
        success: {
          DEFAULT: '#10B981',
        },
        danger: {
          DEFAULT: '#FF3B30',
        },
        warning: '#D4F53C',
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
