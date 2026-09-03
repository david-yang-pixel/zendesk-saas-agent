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

<style scoped>
.metric-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  transition: all 0.15s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
}
.metric-card:hover {
  border-color: rgba(0, 0, 0, 0.16);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.metric-card__top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.metric-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: grid;
  place-items: center;
}
.metric-icon--blue { background: rgba(0, 113, 227, 0.1); color: #0071e3; }
.metric-icon--green { background: rgba(52, 199, 89, 0.12); color: #34c759; }
.metric-icon--amber { background: rgba(245, 166, 35, 0.12); color: #d97706; }
.metric-icon--violet { background: rgba(175, 82, 222, 0.12); color: #af52de; }
.metric-icon--red { background: rgba(255, 59, 48, 0.12); color: #ff3b30; }

.metric-trend {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.trend-success { background: rgba(52, 199, 89, 0.12); color: #2e7d32; }
.trend-warning { background: rgba(245, 166, 35, 0.12); color: #b45309; }
.trend-danger { background: rgba(255, 59, 48, 0.12); color: #c62828; }
.trend-neutral { background: rgba(0, 0, 0, 0.05); color: #6e6e73; }

.metric-label {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 500;
  color: #86868b;
  letter-spacing: -0.01em;
}

.metric-value {
  margin: 0 0 4px;
  font-size: 32px;
  font-weight: 700;
  color: #1d1d1f;
  letter-spacing: -0.03em;
  line-height: 1.15;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
}

.metric-detail {
  margin: 0;
  font-size: 12px;
  color: #6e6e73;
}
</style>
