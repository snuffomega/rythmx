interface ToggleProps {
  on: boolean;
  onChange: (v: boolean) => void;
}

export function Toggle({ on, onChange }: ToggleProps) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative inline-flex items-center w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${on ? 'bg-accent' : 'bg-[#2a2a2a]'}`}
    >
      <span className={`inline-block w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
    </button>
  );
}
