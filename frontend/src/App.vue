<script setup lang="ts">
import { watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import AppNav from './components/AppNav.vue'
import Toast from './components/Toast.vue'
import { updateDocumentTitle } from './router'

const { locale } = useI18n()
const route = useRoute()
watch(locale, () => updateDocumentTitle())
</script>

<template>
  <AppNav v-if="!route.meta.bare" />
  <main>
    <RouterView />
  </main>
  <Toast />
</template>
