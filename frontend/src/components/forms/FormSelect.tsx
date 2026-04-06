import { useId, type SelectHTMLAttributes } from 'react'
import { FormField } from './FormField'

interface SelectOption {
  value: string | number
  label: string
}

interface FormSelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  helperText?: string
  options: SelectOption[]
}

export function FormSelect({ label, helperText, options, className, ...props }: FormSelectProps) {
  const id = useId()
  return (
    <FormField label={label} helperText={helperText} htmlFor={id}>
      <select id={id} className={`select${className ? ` ${className}` : ''}`} {...props}>
        {options.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
    </FormField>
  )
}
