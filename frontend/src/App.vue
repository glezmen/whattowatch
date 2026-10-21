<script setup lang="ts">
import AppNav from './components/AppNav.vue'
import Toast from './components/Toast.vue'
</script>

<template>
  <AppNav />
  <main>
    <RouterView />
  </main>
  <Toast />
</template>
