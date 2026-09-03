<script setup lang="ts">
import AppIcon from './AppIcon.vue'
import type { MetricCard as MetricCardData } from '../types'

defineProps<{ metric: MetricCardData }>()
</script>

<template>
  <article class="metric-card" data-testid="metric-card">
    <div class="metric-card__top">
      <span class="metric-icon" :class="`metric-icon--${metric.accent}`">
        <AppIcon :name="metric.icon" :size="18" />
      </span>
      <span v-if="metric.trend" class="metric-trend" :class="`trend-${metric.trendTone ?? 'neutral'}`">
        <AppIcon v-if="metric.trendTone === 'success'" name="arrow-up" :size="12" />
        {{ metric.trend }}
      </span>
    </div>
    <p class="metric-label">{{ metric.label }}</p>
    <p class="metric-value">{{ metric.value }}</p>
    <p class="metric-detail">{{ metric.detail }}</p>
  </article>
</template>
