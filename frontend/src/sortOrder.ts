import { ref, watch, type Ref } from 'vue'

export type SortOrder = 'default' | 'leaving' | 'rating'

const DEFAULT_SORT: SortOrder = 'leaving'

function isSortOrder(v: unknown): v is SortOrder {
  return v === 'default' || v === 'leaving' || v === 'rating'
}

/** Per-tab sort order, persisted in localStorage under `sort_<tab>`. */
export function useSortOrder(tab: string): Ref<SortOrder> {
  const key = `sort_${tab}`
  let initial: SortOrder = DEFAULT_SORT
  try {
    const v = localStorage.getItem(key)
    if (isSortOrder(v)) initial = v
  } catch {
    // storage unavailable
  }
  const sort = ref<SortOrder>(initial)
  watch(sort, (v) => {
    try {
      localStorage.setItem(key, v)
    } catch {
      // storage unavailable
    }
  })
  return sort
}
