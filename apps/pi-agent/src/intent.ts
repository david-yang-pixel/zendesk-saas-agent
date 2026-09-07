/**
 * Refined Human Handoff Intent Detection
 *
 * Replaces crude substring matching (`text.includes('human')`) with structured,
 * context-aware multi-language intent recognition and negative filtering
 * (preventing false positives on technical discussions like "human-in-the-loop").
 */

// Patterns indicating technical/product questions where keywords might appear innocently
const NEGATIVE_TECHNICAL_PATTERNS = [
  // English technical terms
  /\bhuman[\s_-]in[\s_-]the[\s_-]loop\b/i,
  /\bhuman[\s_-]eval(?:uation)?\b/i,
  /\bhuman[\s_-]feedback\b/i,
  /\brlhf\b/i,
  /\btest(?:ing)?\s+human\b/i,
  /\bcompare\s+(?:with\s+)?human\b/i,
  /\bhuman\s+vs\s+ai\b/i,
  /\bautomated\s+(?:customer\s+)?service\b/i,
  /\bcustomer\s+service\s+(?:system|bot|automation|tool|api|webhook|workflow|integration|plugin)\b/i,

  // Chinese technical terms
  /人机[协辅]同/i,
  /人工[审查]核/i,
  /人工干预/i,
  /人工标注/i,
  /客服[系平]统/i,
  /客服机器人/i,
  /智能客服/i,
  /对接(?:外部)?客服/i,
  /集成客服/i,
  /客服[接插]口/i,
  /客服插件/i,
  /做(?:一个)?客服/i,
  /搭建(?:一个)?客服/i,
  /开发(?:一个)?客服/i,

  // Japanese technical terms
  /カスタマーサポート(?:システム|ボット|自動化|連携|プラグイン)/i,
  /有人(?:判定|チェック|レビュー)/i,
];

// Positive handoff intent patterns
const POSITIVE_INTENT_PATTERNS: RegExp[] = [
  // 1. English handoff intents
  /\b(?:transfer|connect|switch|pass)\s+(?:me\s+)?(?:to\s+)?(?:(?:a|an)\s+)?(?:human|agent|person|representative|operator|live\s+support)\b/i,
  /\b(?:talk|speak|chat)\s+(?:to|with)\s+(?:(?:a|an)\s+)?(?:human|agent|person|representative|operator|real\s+person|someone\s+else)\b/i,
  /\b(?:need|want|get|request)\s+(?:(?:a|an)\s+)?(?:human|real\s+person|live\s+agent|human\s+support|human\s+agent|representative)\b/i,
  /\b(?:human|live)\s+(?:agent|support|representative|operator|help)\s+(?:please|needed|now)?\b/i,
  /\breal\s+person\s+(?:please|now)?\b/i,
  /\bcan\s+i\s+(?:talk|speak)\s+to\s+(?:someone|a\s+human)\b/i,

  // 2. Chinese handoff intents
  /(?:转|找|呼叫|切换到|接入|连线|需要|叫|请帮我转|我要)(?:一个)?(?:人工客服|真人客服|在线客服|人工服务|真人服务|人工)/i,
  /(?:人工客服|真人客服|人工服务|真人服务)(?:在哪|在吗|出来|请问|帮我|解决一下)/i,
  /(?:需要|请|让)(?:人工|真人)(?:介入|接管|处理|回答|协助)/i,
  /^(?:转?人工|人工客服|找人工|呼叫人工|人工帮助|人工服务|真人)$/i,

  // 3. Japanese handoff intents
  /(?:オペレーター|担当者|人間|スタッフ|サポート窓口)(?:に|と|を)?(?:代わって|繋いで|話したい|話せます|話せる|連絡したい|相談したい|交代|呼んで)/i,
  /(?:人間|真人|有人)(?:のサポート|のオペレーター|の対応)/i,
  /^(?:オペレーター|担当者|有人サポート)$/i,

  // 4. Spanish / Portuguese basics
  /\b(?:hablar|falar)\s+con\s+(?:(?:un|uma)\s+)?(?:humano|agente|persona|atendente)\b/i,
  /\b(?:transferir|conectar)\s+(?:a\s+(?:un|uma)\s+)?(?:humano|agente|atendente)\b/i,
];

/**
 * Returns true if the message indicates that the user is requesting human support.
 */
export function detectHumanHandoffIntent(text: string): boolean {
  if (!text || typeof text !== 'string') return false;
  const trimmed = text.trim();
  if (!trimmed) return false;

  // 1. Check positive patterns
  const matchesPositive = POSITIVE_INTENT_PATTERNS.some(regex => regex.test(trimmed));
  if (!matchesPositive) {
    return false;
  }

  // 2. If positive matched, check if it's purely a technical inquiry
  // Only apply negative check if the message has technical context AND does NOT have an explicit "transfer" action
  const hasStrongTransferAction = /(?:转|呼叫|接入|切换到|我要找|transfer\s+(?:me\s+)?to|talk\s+to\s+(?:a\s+)?human|connect\s+me\s+to|代わって|繋いで)/i.test(trimmed);
  if (!hasStrongTransferAction) {
    const isTechnical = NEGATIVE_TECHNICAL_PATTERNS.some(regex => regex.test(trimmed));
    if (isTechnical) {
      return false;
    }
  }

  return true;
}
