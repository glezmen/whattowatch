<script setup lang="ts">
import { computed } from 'vue'
import { AVATAR_COLORS } from '../auth'

const props = withDefaults(defineProps<{ name: string; color?: string | null; size?: number }>(), {
  color: null,
  size: 32,
})

const initial = computed(() => (props.name.trim().charAt(0) || '?').toUpperCase())
const bg = computed(() => props.color || AVATAR_COLORS[0])
</script>

<template>
  <span
    class="avatar"
    aria-hidden="true"
    :style="{ background: bg, width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.45)}px` }"
  >{{ initial }}</span>
</template>

<style scoped>
.avatar {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #fff;
  font-weight: 700;
  line-height: 1;
  user-select: none;
}
</style>
