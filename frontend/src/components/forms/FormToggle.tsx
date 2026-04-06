import { Toggle } from '../common/Toggle'

interface FormToggleProps {
  label: string
  description?: string
  on: boolean
  onChange: (on: boolean) => void
  disabled?: boolean
}

export function FormToggle({ label, description, on, onChange, disabled }: FormToggleProps) {
  return (
    <div className="flex items-center gap-3">
      <Toggle on={on} onChange={onChange} disabled={disabled} />
      <div>
        <p className="text-text-primary text-sm font-medium">{label}</p>
        {description && <p className="text-text-muted text-xs mt-0.5">{description}</p>}
      </div>
    </div>
  )
}
