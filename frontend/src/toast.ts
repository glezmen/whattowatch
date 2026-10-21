import { reactive } from 'vue'

export interface ToastMessage {
  id: number
  text: string
  kind: 'error' | 'info'
}

let next = 1
export const toasts = reactive<ToastMessage[]>([])

export function dismissToast(id: number): void {
  const i = toasts.findIndex((t) => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export function pushToast(text: string, kind: ToastMessage['kind'] = 'info'): void {
  const id = next++
  toasts.push({ id, text, kind })
  if (kind === 'info') setTimeout(() => dismissToast(id), 4000)
}

export function pushError(text: string): void {
  pushToast(text, 'error')
}
