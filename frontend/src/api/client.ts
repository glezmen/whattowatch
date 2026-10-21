// Typed wrappers for the backend contract in docs/api.md (prefix /api).
import { t, te } from '../i18n'
import { pushError } from '../toast'

export type MediaType = 'movie' | 'tv'
export type Confidence = 'verified' | 'stale' | 'estimated' | 'conflicting' | 'user_denied'

export interface AvailabilityEntry {
  provider_key: string
  provider_name: string
  offer_type: string
  source: string
  confidence: Confidence | string
  last_seen_at: string
  leaving_at?: string | null
  leaving_confidence?: 'exact' | 'estimated' | null
  arriving_at?: string | null
  arriving_confidence?: 'exact' | 'estimated' | null
  /** False when the provider is not among the enabled subscriptions (M7). */
  enabled?: boolean
  deep_link?: string | null
}

export interface PersonalRating {
  source: string
  scale: string
  raw_value: string
  rated_at?: string | null
}

export interface UserState {
  seen: boolean
  seen_source?: 'import' | 'manual' | null
  abandoned: boolean
  not_interested: boolean
  stars?: number | null
  liked_text?: string | null
  disliked_text?: string | null
  on_watchlist: boolean
  watchlist_sources: string[]
  personal_ratings: PersonalRating[]
}

export interface CommunityScores {
  tmdb_vote_average?: number | null
  tmdb_vote_count?: number | null
  imdb_rating?: number | null
  imdb_votes?: number | null
  fetched_at?: string | null
}

export interface TitleCard {
  id: number
  media_type: MediaType
  tmdb_id: number
  imdb_id?: string | null
  title_hu?: string | null
  original_title: string
  title_en?: string | null
  has_hu_translation?: boolean | null
  year?: number | null
  genres: string[]
  overview_hu?: string | null
  poster_path?: string | null
  runtime?: number | null
  number_of_seasons?: number | null
  community: CommunityScores
  availability: AvailabilityEntry[]
  user: UserState
}

export interface TitleLinkItem {
  provider_key: string
  offer_type: string
  url: string
  source: string
  fetched_at: string
}

export interface TitleLinks {
  items: TitleLinkItem[]
  fallback_search: Record<string, string>
  refreshed: boolean
}

export interface ReasonCode {
  code: string
  params?: Record<string, unknown> | null
}

export interface RecommendationItem {
  rank: number
  score: number
  /** Hungarian sentences (kept for the CLI); the UI prefers reason_codes. */
  reasons: string[]
  reason_codes?: ReasonCode[] | null
  components: Record<string, number>
  title: TitleCard
  batch_id: string
  generated_at: string
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface PreferenceSignal {
  id: number
  title_id?: number
  feedback_id?: number
  polarity: '+' | '-'
  category: string
  label: string
  /** Human-readable (Hungarian) label, if the vocabulary has one. */
  label_name?: string | null
  weight?: number
  confidence?: number | null
  extractor?: string
  model_version?: string
  prompt_version?: string | null
}

/** PUT /titles/{id}/feedback: "UserState + kinyert jelzések". */
export interface FeedbackResponse {
  user: UserState
  signals: PreferenceSignal[]
}

export interface SearchHit {
  media_type: MediaType
  tmdb_id: number
  title: string
  original_title: string
  year?: number | null
  popularity?: number
  vote_count?: number
  /** Local title id if the hit is already in the catalog. */
  title_id?: number | null
}

export interface ResolutionCandidate {
  media_type: MediaType
  tmdb_id: number
  title: string
  original_title?: string
  year?: number | null
  score?: number
  on_provider?: boolean | null
  poster_path?: string | null
  overview?: string | null
  vote_average?: number | null
  vote_count?: number | null
  popularity?: number | null
  genre_ids?: number[]
  tmdb_url?: string | null
}

export interface ResolutionSeason {
  season: number | null
  episodes: number
  episode_titles: string[]
}

/** What the linked import rows say (viewing: dates, duration, devices, seasons). */
export interface ResolutionContext {
  rows: number
  first_at: string | null
  last_at: string | null
  total_seconds: number | null
  devices: string[]
  seasons: ResolutionSeason[]
  standalone: boolean
  source_profiles: string[]
}

/** Live TMDB details of a candidate (not persisted); `error` if that lookup failed. */
export interface CandidateDetails {
  tmdb_id: number
  media_type: MediaType
  runtime: number | null
  number_of_seasons: number | null
  year: number | null
  overview: string | null
  poster_path: string | null
  imdb_id: string | null
  vote_count: number | null
  error: string | null
}

export interface Resolution {
  id: number
  source: string
  lookup_key: string
  title_text?: string | null
  import_rows?: number
  status: string
  method?: string | null
  confidence?: number | null
  candidates: ResolutionCandidate[] | null
  title_id?: number | null
  resolved_at?: string | null
  context?: ResolutionContext
}

export interface ResolutionActionResponse {
  resolution: Resolution
  reimport_needed: boolean
}

export interface WatchEvent {
  source: string
  status: string
  first_watched_at?: string | null
  last_watched_at?: string | null
  total_seconds?: number | null
  episode_count?: number | null
}

export interface HistoryItem {
  title: TitleCard
  events: WatchEvent[]
  last_watched_at?: string | null
}

export interface WatchlistEntry {
  title: TitleCard
  sources: { source: string; added_at?: string | null }[]
  added_at?: string | null
}

export interface SyncRun {
  id: number
  source: string
  started_at: string
  finished_at?: string | null
  status: string
  processed: number
  created: number
  updated: number
  skipped: number
  errors: unknown[]
  file_sha256?: string | null
}

export interface Settings {
  tmdb_api_key_set: boolean
  netflix_profile_name?: string | null
  lm_studio_base_url?: string | null
  recommender?: Record<string, unknown>
  [key: string]: unknown
}

export interface TitleDetails extends TitleCard {
  resolutions?: { id: number; source: string; lookup_key: string; status: string }[]
}

export type LlmProviderName = 'lmstudio' | 'anthropic' | 'openai'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ToolTraceEntry {
  name: string
  arguments: Record<string, unknown>
  result_summary: string
  error?: string | null
  /** Local title ids returned by get_recommendations / search_titles. */
  title_ids: number[]
}

export interface ChatResult {
  reply: string
  tool_trace: ToolTraceEntry[]
  provider: string
  model: string
  usage: Record<string, number>
  rounds_exhausted?: boolean
}

export type UpdateSection = 'new' | 'upcoming' | 'leaving' | 'new_season' | 'watchlist_available'
export type EventConfidence = 'exact' | 'estimated' | 'verified'

export interface CatalogEvent {
  id: number
  event_type: string
  /** YYYY-MM-DD */
  event_date: string
  source: string
  confidence: EventConfidence | string
  provider_key?: string | null
  provider_name?: string | null
  /** May contain `undated: true`. */
  payload: Record<string, unknown>
  detected_at: string
  leaving_at?: string | null
  arriving_at?: string | null
}

export interface UpdateItem {
  event: CatalogEvent
  title: TitleCard
}

export interface UpdatesResponse {
  section: UpdateSection
  items: UpdateItem[]
  source_status: { tmdb: boolean; streaming_availability: boolean }
}

export interface NotificationItem {
  id: number
  event_id: number
  event_type: string
  title: TitleCard
  event_date: string
  dedupe_key: string
  created_at: string
  read_at?: string | null
}

export interface SubscriptionProfile {
  name: string
  primary_lang?: string | null
  rows?: number | null
}

export interface SubscriptionItem {
  key: string
  display_name: string
  enabled: boolean
  tmdb_provider_ids: number[]
  /** M7b: TMDB logo (w92), cached by the backend. */
  logo_url?: string | null
  /** M7b: profiles found in the uploaded export (currently Netflix only). */
  profiles?: SubscriptionProfile[]
  selected_profile?: string | null
  profile_support?: 'export' | 'none'
}

export interface SubscriptionPatchItem {
  key: string
  enabled?: boolean
  selected_profile?: string | null
}

export type SecretKey = 'TMDB_API_KEY' | 'STREAMING_AVAILABILITY_API_KEY' | 'ANTHROPIC_API_KEY' | 'OPENAI_API_KEY'

export interface SecretItem {
  key: SecretKey | string
  set: boolean
  /** Last 4 characters only; the value itself is never returned. */
  hint?: string | null
  label_key: string
}

export interface SecretsResponse {
  items: SecretItem[]
  lm_studio_base_url: string
}

export interface SecretsPatch {
  values?: Partial<Record<SecretKey | string, string | null>>
  lm_studio_base_url?: string
}

export interface LlmSettings {
  provider: LlmProviderName
  model: string | null
  options: {
    lmstudio: { available: boolean; base_url: string; models: string[] }
    anthropic: { key_set: boolean; models: string[] }
    openai: { key_set: boolean; models: string[] }
  }
  cloud_send_feedback_text: boolean
  fallback_allowed: boolean
}

export type LlmSettingsPatch = Partial<
  Pick<LlmSettings, 'provider' | 'model' | 'cloud_send_feedback_text' | 'fallback_allowed'>
>

export interface LlmTestResult {
  ok: boolean
  model?: string | null
  reply?: string | null
  usage?: Record<string, number> | null
  error?: string | null
}

export interface AppSettings {
  ui_language: 'hu' | 'en'
  netflix_profile_name?: string | null
  netflix_profiles: string[]
  onboarding_done: boolean
}

export type AppSettingsPatch = Partial<Pick<AppSettings, 'ui_language' | 'netflix_profile_name' | 'onboarding_done'>>

export type ImportSourceKey = 'imdb_ratings' | 'imdb_watchlist' | 'netflix_export' | string

export interface ImportSource {
  source: ImportSourceKey
  label_key: string
  accepts: string
  supported: boolean
  note_key?: string | null
}

export type ImportJobStatus = 'queued' | 'running' | 'done' | 'failed'

export interface ImportJob {
  id: number | string
  source: ImportSourceKey
  file_name: string
  status: ImportJobStatus | string
  started_at?: string | null
  finished_at?: string | null
  summary?: {
    processed: number
    created: number
    updated: number
    skipped: number
    unresolved?: number
    ambiguous?: number
  } | null
  error?: string | null
  sync_run_id?: number | null
  params?: { profile_name?: string | null } | null
}

export interface NetflixProfile {
  name: string
  primary_lang?: string | null
  viewing_rows: number
}

export type RefreshSummary = Record<string, number | string>

export class ApiError extends Error {
  status: number
  /** `detail_code` from the response body, when the backend sends one. */
  code: string | null
  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

export interface BackupItem {
  name: string
  size_bytes: number
  created_at: string
}

export interface BackupList {
  backups_dir: string
  items: BackupItem[]
  note?: string | null
}

export interface BackupInfo {
  alembic_revision?: string | null
  tables?: number | null
  titles?: number | null
  watch_events?: number | null
  size_bytes?: number | null
  note?: string | null
}

export interface RestoreResult {
  previous_backup?: string | null
  revision_before?: string | null
  revision_after?: string | null
  restored: BackupInfo
  note?: string | null
}

type Query = Record<string, string | number | boolean | null | undefined>

function qs(params: Query): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

function detailText(body: unknown): string | null {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d)) {
      return d
        .map((e) => (e && typeof e === 'object' && 'msg' in e ? String((e as { msg: unknown }).msg) : String(e)))
        .join('; ')
    }
  }
  return null
}

function errorMessage(status: number, body: unknown): string {
  const code =
    body && typeof body === 'object' && 'detail_code' in body ? (body as { detail_code: unknown }).detail_code : null
  if (typeof code === 'string' && te(`errors.code.${code}`)) return t(`errors.code.${code}`)
  const detail = detailText(body)
  if (status === 409) return detail ?? t('errors.conflict')
  if (status === 404) return detail ? t('errors.notFoundDetail', { detail }) : t('errors.notFound')
  if (status === 422) return detail ? t('errors.invalidDetail', { detail }) : t('errors.invalid')
  return detail ? t('errors.serverDetail', { status, detail }) : t('errors.server', { status })
}

async function request<T>(method: string, path: string, body?: unknown, quiet = false): Promise<T> {
  return (await requestWithHeaders<T>(method, path, body, quiet)).data
}

async function requestWithHeaders<T>(
  method: string,
  path: string,
  body?: unknown,
  quiet = false,
): Promise<{ data: T; headers: Headers }> {
  let res: Response
  try {
    const isForm = body instanceof FormData
    res = await fetch(`/api${path}`, {
      method,
      // multipart: the browser sets the boundary itself
      headers: body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : undefined,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    })
  } catch {
    const msg = t('errors.unreachable')
    if (!quiet) pushError(msg)
    throw new ApiError(0, msg)
  }
  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  if (!res.ok) {
    const msg = errorMessage(res.status, data)
    if (!quiet) pushError(msg)
    const code =
      data && typeof data === 'object' && 'detail_code' in data ? (data as { detail_code: unknown }).detail_code : null
    throw new ApiError(res.status, msg, typeof code === 'string' ? code : null)
  }
  return { data: data as T, headers: res.headers }
}

/** GET /recommendations: items plus the X-Filtered-Watchlist count (0 if absent). */
export interface RecommendationsResult {
  items: RecommendationItem[]
  filteredWatchlist: number
}

async function fetchRecommendations(p: Query): Promise<RecommendationsResult> {
  const { data, headers } = await requestWithHeaders<RecommendationItem[]>('GET', `/recommendations${qs(p)}`)
  const n = Number.parseInt(headers.get('X-Filtered-Watchlist') ?? '', 10)
  return { items: data ?? [], filteredWatchlist: Number.isFinite(n) && n > 0 ? n : 0 }
}

const enc = encodeURIComponent

export const api = {
  getRecommendations: (p: { limit?: number; media_type?: MediaType | ''; include_watchlist?: boolean }) =>
    fetchRecommendations({ ...p, refresh: undefined }),
  /** refresh=true: generates a new batch (slow). */
  refreshRecommendations: (p: { limit?: number; media_type?: MediaType | ''; include_watchlist?: boolean }) =>
    fetchRecommendations({ ...p, refresh: true }),
  recommendationFeedback: (batchId: string, titleId: number, feedback: 'clicked' | 'dismissed' | 'watched') =>
    request<unknown>('POST', `/recommendations/${enc(batchId)}/${titleId}/feedback`, { feedback }),

  getTitle: (id: number) => request<TitleDetails>('GET', `/titles/${id}`),
  listTitles: (p: {
    q?: string
    media_type?: MediaType | ''
    provider?: string
    genre?: string
    seen?: boolean | null
    limit?: number
    offset?: number
  }) => request<Page<TitleCard>>('GET', `/titles${qs(p)}`),

  putFeedback: (id: number, body: { liked_text?: string | null; disliked_text?: string | null }) =>
    request<FeedbackResponse>('PUT', `/titles/${id}/feedback`, body),
  getTitleLinks: (id: number) => request<TitleLinks>('GET', `/titles/${id}/links`, undefined, true),
  getSignals: (id: number) => request<PreferenceSignal[]>('GET', `/titles/${id}/signals`),
  deleteSignal: (signalId: number) => request<unknown>('DELETE', `/signals/${signalId}`),
  putRating: (id: number, stars: number | null) => request<UserState>('PUT', `/titles/${id}/rating`, { stars }),
  putSeen: (id: number, seen: boolean) => request<UserState>('PUT', `/titles/${id}/seen`, { seen }),
  putNotInterested: (id: number, value: boolean) =>
    request<UserState>('PUT', `/titles/${id}/not-interested`, { value }),
  addToWatchlist: (id: number) => request<UserState>('POST', `/titles/${id}/watchlist`),
  removeFromWatchlist: (id: number) => request<UserState>('DELETE', `/titles/${id}/watchlist`),

  getWatchlist: (p: { source?: string; available?: boolean; limit?: number; offset?: number }) =>
    request<Page<WatchlistEntry>>('GET', `/watchlist${qs(p)}`),
  getHistory: (p: { q?: string; status?: string; source?: string; exclude_rated?: boolean; limit?: number; offset?: number }) =>
    request<Page<HistoryItem>>('GET', `/history${qs(p)}`),

  searchTmdb: (q: string, media_type?: MediaType | '') =>
    request<SearchHit[]>('GET', `/search/tmdb${qs({ q, media_type })}`),
  ensureTitle: (media_type: MediaType, tmdb_id: number) =>
    request<TitleCard>('POST', '/titles/ensure', { media_type, tmdb_id }),

  getResolutions: (p: { status?: 'ambiguous' | 'unresolved'; source?: string; limit?: number; offset?: number }) =>
    request<Page<Resolution>>('GET', `/resolutions${qs(p)}`),
  setResolution: (id: number, media_type: MediaType, tmdb_id: number) =>
    request<ResolutionActionResponse>('POST', `/resolutions/${id}/set`, { media_type, tmdb_id }),
  rejectResolution: (id: number) => request<ResolutionActionResponse>('POST', `/resolutions/${id}/reject`),
  getCandidateDetails: (id: number) => request<CandidateDetails[]>('GET', `/resolutions/${id}/candidates/details`),

  getSyncRuns: (limit = 50) => request<SyncRun[] | Page<SyncRun>>('GET', `/sync/runs${qs({ limit })}`),
  getSettings: () => request<Settings>('GET', '/settings'),
  postChat: (messages: ChatMessage[], provider?: LlmProviderName | string) =>
    request<ChatResult>('POST', '/chat', { messages, provider: provider || undefined }),

  getUpdates: (section: UpdateSection, days = 31, limit = 50) =>
    request<UpdatesResponse>('GET', `/updates${qs({ section, days, limit })}`),
  getNotifications: (unread = true, limit = 50) =>
    request<NotificationItem[]>('GET', `/notifications${qs({ unread, limit })}`),
  markNotificationRead: (id: number) => request<NotificationItem>('POST', `/notifications/${id}/read`),
  /** Polled in the background: quiet (no toast on failure). */
  getNotificationCount: () => request<{ unread: number }>('GET', '/notifications/count', undefined, true),
  postRefresh: () => request<RefreshSummary>('POST', '/refresh'),

  getSubscriptions: () => request<{ items: SubscriptionItem[] }>('GET', '/settings/subscriptions'),
  putSubscriptions: (items: SubscriptionPatchItem[]) =>
    request<{ items: SubscriptionItem[] } | unknown>('PUT', '/settings/subscriptions', { items }),
  getAppSettings: (quiet = false) => request<AppSettings>('GET', '/settings/app', undefined, quiet),
  putAppSettings: (patch: AppSettingsPatch, quiet = false) =>
    request<AppSettings | unknown>('PUT', '/settings/app', patch, quiet),

  getSecrets: () => request<SecretsResponse>('GET', '/settings/secrets'),
  /** Quiet: the caller shows errors inline (e.g. invalid_tmdb_key). */
  putSecrets: (patch: SecretsPatch) => request<SecretsResponse>('PUT', '/settings/secrets', patch, true),
  getLlmSettings: () => request<LlmSettings>('GET', '/settings/llm'),
  putLlmSettings: (patch: LlmSettingsPatch) => request<LlmSettings>('PUT', '/settings/llm', patch),
  /** Runs one short real request against the provider: only on explicit user action. Quiet: shown inline. */
  testLlm: (body: { provider?: LlmProviderName; model?: string | null } = {}) =>
    request<LlmTestResult>('POST', '/settings/llm/test', body, true),

  getImportSources: () => request<ImportSource[]>('GET', '/import/sources'),
  getImportJobs: (limit = 20, quiet = false) =>
    request<ImportJob[]>('GET', `/import/jobs${qs({ limit })}`, undefined, quiet),
  getImportJob: (id: number | string) => request<ImportJob>('GET', `/import/jobs/${enc(String(id))}`),
  uploadImport: (source: string, file: File, profileName?: string | null) => {
    const fd = new FormData()
    fd.set('source', source)
    fd.set('file', file)
    if (profileName) fd.set('profile_name', profileName)
    return request<ImportJob>('POST', '/import/upload', fd)
  },
  netflixProfiles: (file: File) => {
    const fd = new FormData()
    fd.set('file', file)
    return request<{ profiles: NetflixProfile[] }>('POST', '/import/netflix/profiles', fd)
  },

  /** Navigate an <a download> here; this helper is only the URL. */
  backupExportUrl: '/api/backup/export',
  listBackups: () => request<BackupList>('GET', '/backup/list'),
  /** Quiet: errors (invalid_backup) are shown inline. */
  inspectBackup: (file: File) => {
    const fd = new FormData()
    fd.set('file', file)
    return request<BackupInfo>('POST', '/backup/inspect', fd, true)
  },
  /** Quiet: errors (confirm_required, invalid_backup) are shown inline. */
  restoreBackup: (file: File, confirm: string) => {
    const fd = new FormData()
    fd.set('file', file)
    fd.set('confirm', confirm)
    return request<RestoreResult>('POST', '/backup/restore', fd, true)
  },

  health: () => request<unknown>('GET', '/health', undefined, true),
}

/** Some list endpoints are not specified as Page<T>; accept both shapes. */
export function itemsOf<T>(data: T[] | Page<T>): T[] {
  return Array.isArray(data) ? data : data.items
}
