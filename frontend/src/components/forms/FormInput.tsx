import { useId, type InputHTMLAttributes } from 'react'
import { FormField } from './FormField'

interface FormInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  helperText?: string
}

export function FormInput({ label, helperText, className, ...props }: FormInputProps) {
  const id = useId()
  return (
    <FormField label={label} helperText={helperText} htmlFor={id}>
      <input id={id} className={`input${className ? ` ${className}` : ''}`} {...props} />
    </FormField>
  )
}
