<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import {
  api,
  type ChatMessage,
  type ChatResult,
  type LlmProviderName,
  type TitleCard as TitleCardData,
} from '../api/client'
import TitleCard from '../components/TitleCard.vue'

interface Turn {
  id: number
  role: 'user' | 'assistant'
  content: string
  result?: ChatResult
  cards?: TitleCardData[]
  cardsLoading?: boolean
}

const MAX_CARDS = 8
const CARD_TOOLS = new Set(['get_recommendations', 'search_titles'])
const providers: { value: LlmProviderName; label: string }[] = [
  { value: 'lmstudio', label: 'LM Studio (helyi)' },
  { value: 'anthropic', label: 'Anthropic (felhő)' },
  { value: 'openai', label: 'OpenAI (felhő)' },
]

const turns = ref<Turn[]>([])
const draft = ref('')
const provider = ref<LlmProviderName>('lmstudio')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)
let nextId = 1

onMounted(async () => {
  try {
    const s = await api.getSettings()
    const p = s.llm_provider
    if (typeof p === 'string' && providers.some((x) => x.value === p)) provider.value = p as LlmProviderName
  } catch {
    // toast already shown; keep the default
  }
})

async function scrollToEnd() {
  await nextTick()
  const el = listEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function titleIds(result: ChatResult): number[] {
  const ids: number[] = []
  for (const t of result.tool_trace) {
    if (!CARD_TOOLS.has(t.name)) continue
    for (const id of t.title_ids) if (!ids.includes(id)) ids.push(id)
  }
  return ids.slice(0, MAX_CARDS)
}

async function loadCards(turn: Turn) {
  const ids = turn.result ? titleIds(turn.result) : []
  if (!ids.length) return
  turn.cardsLoading = true
  const res = await Promise.allSettled(ids.map((id) => api.getTitle(id)))
  turn.cards = res.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
  turn.cardsLoading = false
}

async function send() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  turns.value.push({ id: nextId++, role: 'user', content: text })
  draft.value = ''
  sending.value = true
  void scrollToEnd()
  const history: ChatMessage[] = turns.value.map((t) => ({ role: t.role, content: t.content }))
  try {
    const result = await api.postChat(history, provider.value)
    turns.value.push({ id: nextId++, role: 'assistant', content: result.reply, result })
    const turn = turns.value[turns.value.length - 1]!
    void scrollToEnd()
    await loadCards(turn)
  } catch {
    // The error toast (incl. the server's 409 message) is shown by the client;
    // give the text back so it can be resent.
    turns.value.pop()
    draft.value = text
  } finally {
    sending.value = false
    void scrollToEnd()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
  }
}

function clearChat() {
  turns.value = []
}

function fmtArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args)
}
</script>

<template>
  <h1>Beszélgetős ajánló</h1>
  <div class="toolbar">
    <label class="small">
      Modell:
      <select v-model="provider" :disabled="sending" aria-label="LLM-szolgáltató">
        <option v-for="p in providers" :key="p.value" :value="p.value">{{ p.label }}</option>
      </select>
    </label>
    <button type="button" :disabled="sending || !turns.length" @click="clearChat">Új beszélgetés</button>
    <span class="small muted">
      A címeket és az elérhetőséget a backend adja; a modell csak megfogalmazza a választ.
    </span>
  </div>

  <div ref="listEl" class="chat" aria-live="polite">
    <p v-if="!turns.length" class="empty">
      Kérdezz bátran, pl. „Valami könnyed vígjáték estére a Netflixen” vagy „Mi van a watchlistemen, ami most
      elérhető?”
    </p>
    <div v-for="t in turns" :key="t.id" class="turn" :class="t.role">
      <div class="bubble">{{ t.content }}</div>
      <template v-if="t.role === 'assistant' && t.result">
        <p class="small muted meta">
          {{ t.result.provider }} · {{ t.result.model }}
          <template v-if="t.result.rounds_exhausted"> · eszközkorlát elérve</template>
        </p>
        <details v-if="t.result.tool_trace.length" class="trace small">
          <summary>Eszközhívások ({{ t.result.tool_trace.length }})</summary>
          <ol>
            <li v-for="(e, i) in t.result.tool_trace" :key="i" :class="{ err: !!e.error }">
              <code>{{ e.name }}({{ fmtArgs(e.arguments) }})</code> → {{ e.result_summary }}
            </li>
          </ol>
        </details>
        <p v-if="t.cardsLoading" class="small muted"><span class="spinner"></span> Kártyák betöltése…</p>
        <div v-else-if="t.cards && t.cards.length" class="grid cards">
          <TitleCard v-for="c in t.cards" :key="c.id" :title="c" />
        </div>
      </template>
    </div>
    <div v-if="sending" class="turn assistant">
      <div class="bubble muted"><span class="spinner"></span> Gondolkodom…</div>
    </div>
  </div>

  <form class="composer" @submit.prevent="send">
    <textarea
      v-model="draft"
      rows="3"
      placeholder="Írd le, mit néznél… (Enter: küldés, Shift+Enter: új sor)"
      aria-label="Üzenet"
      @keydown="onKeydown"
    ></textarea>
    <button type="submit" class="primary" :disabled="sending || !draft.trim()">Küldés</button>
  </form>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 200px;
  max-height: calc(100vh - 300px);
  overflow-y: auto;
  padding: 4px 2px 12px;
}
.turn {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.turn.user {
  align-items: flex-end;
}
.turn.assistant {
  align-items: flex-start;
}
.bubble {
  max-width: min(720px, 90%);
  padding: 8px 12px;
  border-radius: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  border: 1px solid var(--border);
  background: var(--surface);
}
.turn.user .bubble {
  background: var(--accent);
  color: var(--accent-text);
  border-color: var(--accent);
}
.meta {
  margin: 0;
}
.trace {
  max-width: min(720px, 90%);
}
.trace summary {
  cursor: pointer;
  color: var(--text-muted);
}
.trace ol {
  margin: 4px 0 0;
  padding-left: 20px;
}
.trace code {
  overflow-wrap: anywhere;
}
.trace .err {
  color: var(--danger);
}
.cards {
  width: 100%;
}
.composer {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  position: sticky;
  bottom: 0;
  padding: 8px 0;
  background: var(--bg);
}
.composer textarea {
  flex: 1;
  min-width: 0;
}
</style>
