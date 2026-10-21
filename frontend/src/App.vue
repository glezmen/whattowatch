<script setup lang="ts">
import { watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppNav from './components/AppNav.vue'
import Toast from './components/Toast.vue'
import { updateDocumentTitle } from './router'

const { locale } = useI18n()
watch(locale, () => updateDocumentTitle())
</script>

<template>
  <AppNav />
  <main>
    <RouterView />
  </main>
  <Toast />
</template>
