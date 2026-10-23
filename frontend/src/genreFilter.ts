import { ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

/** Genre the current list page is filtered to (page-session state; cleared on navigation). */
export const activeGenre = ref('')

/** Genre chip click: filter the current list page, or jump to Discover on pages without a list. */
export function selectGenre(genre: string, router: Router, route: RouteLocationNormalizedLoaded): void {
  if (route.meta.genreList === true) {
    activeGenre.value = activeGenre.value === genre ? '' : genre
  } else {
    void router.push({ path: '/felfedezes', query: { genre } })
  }
}
