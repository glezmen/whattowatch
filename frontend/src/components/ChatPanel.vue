<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { CHAT_PROVIDERS, chat, clearChat, loadDefaultProvider, sendChat } from '../chatStore'
import TitleCard from './TitleCard.vue'

const props = defineProps<{ compact?: boolean }>()
const { t } = useI18n()

const expanded = ref(false)
const listEl = ref<HTMLElement | null>(null)

/** Compact mode shows only the latest exchange (last user message and what follows) unless expanded. */
const shownTurns = computed(() => {
  if (!props.compact || expanded.value) return chat.turns
  let i = chat.turns.length - 1
  while (i > 0 && chat.turns[i]!.role !== 'user') i--
  return chat.turns.slice(Math.max(0, i))
})
const hiddenCount = computed(() => chat.turns.length - shownTurns.value.length)

onMounted(() => {
  void loadDefaultProvider(true)
  void scrollToEnd()
})

async function scrollToEnd() {
  await nextTick()
  const el = listEl.value
  if (el) el.scrollTop = el.scrollHeight
}

watch(
  () => [chat.turns.length, chat.sending, expanded.value],
  () => void scrollToEnd(),
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void sendChat()
  }
}

function fmtArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args)
}
</script>

<template>
  <section class="chat-panel" :class="{ compact }">
    <div v-if="compact" class="head">
      <h2>{{ t('chat.title') }}</h2>
      <button v-if="hiddenCount > 0 || expanded" type="button" class="link small" :aria-expanded="expanded" @click="expanded = !expanded">
        {{ expanded ? t('chat.showLess') : t('chat.showAll', { n: chat.turns.length }) }}
      </button>
      <button v-if="chat.turns.length" type="button" class="link small" :disabled="chat.sending" @click="clearChat">
        {{ t('chat.newChat') }}
      </button>
      <RouterLink to="/beszelgetes" class="small open">{{ t('chat.openFull') }}</RouterLink>
    </div>
    <div v-else class="toolbar">
      <label class="small">
        {{ t('chat.model') }}
        <select v-model="chat.provider" :disabled="chat.sending" :aria-label="t('chat.providerLabel')">
          <option v-for="p in CHAT_PROVIDERS" :key="p" :value="p">{{ t(`chat.providers.${p}`) }}</option>
        </select>
      </label>
      <button type="button" :disabled="chat.sending || !chat.turns.length" @click="clearChat">{{ t('chat.newChat') }}</button>
      <span class="small muted">{{ t('chat.backendNote') }}</span>
    </div>

    <div v-if="!compact || chat.turns.length || chat.sending" ref="listEl" class="chat" aria-live="polite">
      <p v-if="!chat.turns.length && !chat.sending" class="empty">{{ t('chat.empty') }}</p>
      <div v-for="turn in shownTurns" :key="turn.id" class="turn" :class="turn.role">
        <div class="bubble">{{ turn.content }}</div>
        <template v-if="turn.role === 'assistant' && turn.result">
          <p class="small muted meta">
            {{ turn.result.provider }} · {{ turn.result.model }}
            <template v-if="turn.result.rounds_exhausted"> · {{ t('chat.roundsExhausted') }}</template>
          </p>
          <details v-if="turn.result.tool_trace.length" class="trace small">
            <summary>{{ t('chat.toolCalls', { n: turn.result.tool_trace.length }) }}</summary>
            <ol>
              <li v-for="(e, i) in turn.result.tool_trace" :key="i" :class="{ err: !!e.error }">
                <code>{{ e.name }}({{ fmtArgs(e.arguments) }})</code> → {{ e.result_summary }}
              </li>
            </ol>
          </details>
          <p v-if="turn.cardsLoading" class="small muted"><span class="spinner"></span> {{ t('chat.loadingCards') }}</p>
          <template v-else-if="turn.cards && turn.cards.length">
            <p v-if="turn.result.recommended_title_ids_source === 'text'" class="small muted meta">
              {{ t('chat.cardsFromText') }}
            </p>
            <div class="grid cards">
              <TitleCard v-for="c in turn.cards" :key="c.id" :title="c" />
            </div>
          </template>
        </template>
      </div>
      <div v-if="chat.sending" class="turn assistant">
        <div class="bubble muted"><span class="spinner"></span> {{ t('chat.thinking') }}</div>
      </div>
    </div>

    <form class="composer" @submit.prevent="sendChat">
      <input
        v-if="compact"
        v-model="chat.draft"
        type="text"
        :placeholder="t('chat.placeholderCompact')"
        :aria-label="t('chat.messageLabel')"
        @keydown="onKeydown"
      />
      <textarea
        v-else
        v-model="chat.draft"
        rows="3"
        :placeholder="t('chat.placeholder')"
        :aria-label="t('chat.messageLabel')"
        @keydown="onKeydown"
      ></textarea>
      <button type="submit" class="primary" :disabled="chat.sending || !chat.draft.trim()">{{ t('chat.send') }}</button>
    </form>
  </section>
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
.compact {
  margin-bottom: 20px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.compact .chat {
  min-height: 0;
  max-height: 60vh;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  margin-bottom: 8px;
}
.head h2 {
  margin: 0;
  font-size: 1.05rem;
}
.head .open {
  margin-left: auto;
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
.compact .bubble {
  background: var(--surface-2);
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
.compact .composer {
  position: static;
  padding: 0;
  background: none;
  align-items: center;
}
.composer textarea,
.composer input {
  flex: 1;
  min-width: 0;
}
</style>
