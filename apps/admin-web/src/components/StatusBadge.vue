<script setup lang="ts">
import type { StatusTone } from '../types'

withDefaults(defineProps<{ label: string; tone?: StatusTone; dot?: boolean }>(), {
  tone: 'neutral',
  dot: true,
})
</script>

<template>
  <span class="status-badge" :class="`status-${tone}`">
    <span v-if="dot" class="status-dot" aria-hidden="true" />
    {{ label }}
  </span>
</template>
