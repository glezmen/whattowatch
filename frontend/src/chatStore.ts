// Module-level conversation state, shared by the home-page panel and /beszelgetes,
// so switching pages (or an in-flight request) keeps the conversation.
import { reactive } from 'vue'
import { api, type ChatMessage, type ChatResult, type LlmProviderName, type TitleCard } from './api/client'

export interface Turn {
  id: number
  role: 'user' | 'assistant'
  content: string
  result?: ChatResult
  cards?: TitleCard[]
  cardsLoading?: boolean
}

export const CHAT_PROVIDERS: LlmProviderName[] = ['lmstudio', 'anthropic', 'openai']
const MAX_CARDS = 8

export const chat = reactive<{
  turns: Turn[]
  draft: string
  provider: LlmProviderName
  providerLoaded: boolean
  sending: boolean
}>({
  turns: [],
  draft: '',
  provider: 'lmstudio',
  providerLoaded: false,
  sending: false,
})

let nextId = 1

/** Default provider from GET /settings, once per session. */
export async function loadDefaultProvider(): Promise<void> {
  if (chat.providerLoaded) return
  chat.providerLoaded = true
  try {
    const s = await api.getSettings()
    const p = s.llm_provider
    if (typeof p === 'string' && (CHAT_PROVIDERS as string[]).includes(p)) chat.provider = p as LlmProviderName
  } catch {
    // toast already shown; keep the default
  }
}

/** Cards come only from the titles the model actually recommends (backend-selected, in reply order). */
function titleIds(result: ChatResult): number[] {
  return [...new Set(result.recommended_title_ids ?? [])].slice(0, MAX_CARDS)
}

async function loadCards(turn: Turn): Promise<void> {
  const ids = turn.result ? titleIds(turn.result) : []
  if (!ids.length) return
  turn.cardsLoading = true
  const res = await Promise.allSettled(ids.map((id) => api.getTitle(id)))
  turn.cards = res.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
  turn.cardsLoading = false
}

export async function sendChat(): Promise<void> {
  const text = chat.draft.trim()
  if (!text || chat.sending) return
  chat.turns.push({ id: nextId++, role: 'user', content: text })
  chat.draft = ''
  chat.sending = true
  const history: ChatMessage[] = chat.turns.map((t) => ({ role: t.role, content: t.content }))
  try {
    const result = await api.postChat(history, chat.provider)
    chat.turns.push({ id: nextId++, role: 'assistant', content: result.reply, result })
    chat.sending = false
    // the reactive proxy of the pushed turn, so card loading updates the view
    await loadCards(chat.turns[chat.turns.length - 1]!)
  } catch {
    // The error toast (incl. the server's 409 message) is shown by the client;
    // give the text back so it can be resent.
    chat.turns.pop()
    chat.draft = text
  } finally {
    chat.sending = false
  }
}

export function clearChat(): void {
  chat.turns = []
}
