<script setup lang="ts">
defineProps<{ heading: string; what?: string }>()
</script>

<template>
  <h1>{{ heading }}</h1>
  <div v-if="what" class="card">
    <p>
      Ehhez a nézethez ({{ what }}) még nincs beállított, megbízható adatforrás. Az M6 mérföldkőben készül el az ütemezett
      katalógusfrissítés és a változások felismerése.
    </p>
    <p class="small muted">
      Kitalált adatot nem jelenítünk meg: kivezetési dátum csak igazolt forrásból kerülhet ide.
    </p>
  </div>
  <p v-else class="empty"><RouterLink to="/">Vissza a főoldalra</RouterLink></p>
</template>
