import { createRouter, createWebHistory } from 'vue-router'
import { activeGenre } from './genreFilter'
import { t } from './i18n'
import HomeView from './views/HomeView.vue'

export const router = createRouter({
  history: createWebHistory(),
  // Only hash links (e.g. Settings → #profil); other navigations keep the previous behaviour.
  scrollBehavior(to) {
    if (to.hash) return { el: to.hash, top: 64 }
    return false
  },
  routes: [
    {
      path: '/profil',
      component: () => import('./views/ProfileView.vue'),
      meta: { titleKey: 'profile.pickerTitle', bare: true },
    },
    { path: '/', component: HomeView, meta: { titleKey: 'nav.home', genreList: true } },
    { path: '/cim/:id(\\d+)', component: () => import('./views/TitleView.vue'), meta: { titleKey: 'titlePage.fallbackTitle' } },
    { path: '/felfedezes', component: () => import('./views/DiscoverView.vue'), meta: { titleKey: 'nav.discover', genreList: true } },
    { path: '/beszelgetes', component: () => import('./views/ChatView.vue'), meta: { titleKey: 'nav.chat' } },
    { path: '/watchlist', component: () => import('./views/WatchlistView.vue'), meta: { titleKey: 'nav.watchlist', genreList: true } },
    { path: '/elozmenyek', component: () => import('./views/HistoryView.vue'), meta: { titleKey: 'nav.history', genreList: true } },
    { path: '/feloldas', component: () => import('./views/ResolutionView.vue'), meta: { titleKey: 'nav.resolution' } },
    { path: '/import', component: () => import('./views/ImportView.vue'), meta: { titleKey: 'nav.import' } },
    { path: '/sync', redirect: '/beallitasok' },
    { path: '/beallitasok', component: () => import('./views/SettingsView.vue'), meta: { titleKey: 'nav.settings' } },
    { path: '/ujdonsagok', component: () => import('./views/UpdatesView.vue'), meta: { titleKey: 'nav.updates', genreList: true } },
    { path: '/tavozik', component: () => import('./views/LeavingView.vue'), meta: { titleKey: 'nav.leaving', genreList: true } },
    {
      path: '/:rest(.*)*',
      component: () => import('./views/PlaceholderView.vue'),
      props: { headingKey: 'nav.notFound' },
      meta: { titleKey: 'nav.notFound' },
    },
  ],
})

export function updateDocumentTitle(): void {
  const key = router.currentRoute.value.meta.titleKey
  const app = t('app.name')
  const text = router.currentRoute.value.meta.titleText
  if (typeof text === 'string' && text) {
    document.title = `${text} · ${app}`
    return
  }
  document.title = typeof key === 'string' ? `${t(key)} · ${app}` : app
}

router.afterEach(() => updateDocumentTitle())

router.afterEach(() => {
  activeGenre.value = ''
})
