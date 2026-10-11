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
  // 1. Standalone English handoff keywords / short commands (with optional punctuation & please)
  /^(?:(?:please|can\s+i\s+get|i\s+need|i\s+want)\s+)?(?:human|support|agent|live\s*agent|live\s*support|representative|operator|real\s*person)(?:\s+(?:please|now|help|assist))*[\s\?!.,~]*$/i,

  // 2. English questions asking if current responder is human or bot
  /\b(?:are\s+you|is\s+this|am\s+i\s+talking\s+to)\s+(?:a\s+)?(?:human|bot|robot|ai|machine|real\s+person)\b/i,

  // 3. English phrase handoff intents
  /\b(?:transfer|connect|switch|pass)\s+(?:me\s+)?(?:to\s+)?(?:(?:a|an)\s+)?(?:human|agent|person|representative|operator|live\s+support|someone\s+else)\b/i,
  /\b(?:talk|speak|chat)\s+(?:to|with)\s+(?:(?:a|an)\s+)?(?:human|agent|person|representative|operator|real\s+person|someone\s+else)\b/i,
  /\b(?:need|want|get|request)\s+(?:(?:a|an)\s+)?(?:human|real\s+person|live\s+agent|human\s+support|human\s+agent|representative)\b/i,
  /\b(?:human|live)\s+(?:agent|support|representative|operator|help)\s*(?:please|needed|now)?\b/i,
  /\breal\s+person\s*(?:please|now)?\b/i,
  /\bcan\s+i\s+(?:talk|speak)\s+to\s+(?:someone|a\s+human)\b/i,

  // 4. Chinese handoff intents
  /(?:转|找|呼叫|切换到|接入|连线|需要|叫|请帮我转|我要)(?:一个)?(?:人工客服|真人客服|在线客服|人工服务|真人服务|人工|客服)/i,
  /(?:人工客服|真人客服|人工服务|真人服务|客服)(?:在哪|在吗|出来|请问|帮我|解决一下)/i,
  /(?:需要|请|让)(?:人工|真人)(?:介入|接管|处理|回答|协助)/i,
  /(?:是|是不是|有没有|你们是)(?:人工|真人)(?:吗|客服|？|\?)?/i,
  /(?:有人在吗|有人吗|在线客服|转人工|找客服)/i,
  /^(?:请?转?人工|人工客服|找人工|呼叫人工|人工帮助|人工服务|真人|客服|找客服|转客服)[\s\?!.,~，。？！]*$/i,

  // 5. Japanese handoff intents
  /(?:オペレーター|担当者|人間|スタッフ|サポート窓口)(?:に|と|を)?(?:代わって|繋いで|話したい|話せます|話せる|連絡したい|相談したい|交代|呼んで)/i,
  /(?:人間|真人|有人)(?:のサポート|のオペレーター|の対応)/i,
  /^(?:オペレーター|担当者|有人サポート|サポート|人間)[\s\?!.,~，。？！]*$/i,

  // 6. Spanish / Portuguese basics
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

  // 2. Direct standalone handoff requests bypass negative technical checks
  const isDirectHandoffKeyword =
    /^(?:(?:please|can\s+i\s+get|i\s+need|i\s+want)\s+)?(?:human|support|agent|live\s*agent|live\s*support|representative|operator|real\s*person)(?:\s+(?:please|now|help|assist))*[\s\?!.,~]*$/i.test(trimmed)
    || /^(?:请?转?人工|人工客服|找人工|呼叫人工|人工帮助|人工服务|真人|客服|找客服|转客服)[\s\?!.,~，。？！]*$/i.test(trimmed)
    || /^(?:オペレーター|担当者|有人サポート|サポート|人間)[\s\?!.,~，。？！]*$/i.test(trimmed);

  if (isDirectHandoffKeyword) {
    return true;
  }

  // 3. If positive matched, check if it's purely a technical inquiry
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

export type HumanActionCategory = 'refund' | 'billing_dispute' | 'credit_restore' | 'account_security';

export interface HumanActionDetection {
  required: boolean;
  category?: HumanActionCategory;
}

const REFUND_PATTERNS = [
  /\b(?:request|want|need|apply for|get|ask for)\s+(?:a\s+)?refund\b/i,
  /\brefund\s+(?:my\s+)?(?:subscription|payment|money|charge|credit)\b/i,
  /\bcancel\s+and\s+refund\b/i,
  /\bmoney\s*back\b/i,
  /(?:申请)?退款/u,
  /(?:退钱|退费|退回(?:款项|扣费|费用))/u,
  /(?:返金|払い戻し)/u,
];

const BILLING_DISPUTE_PATTERNS = [
  /\b(?:charged|billed)\s+(?:twice|wrong|incorrectly)\b/i,
  /\b(?:invoice|billing)\s+(?:error|dispute|is wrong|incorrect)\b/i,
  /(?:发票重开|发票金额错误|多扣款|重复扣款|账单有误)/u,
  /(?:二重請求|誤請求)/u,
];

const CREDIT_RESTORE_PATTERNS = [
  /\b(?:restore|replenish|compensate|reimburse|add back)\s+(?:my\s+)?(?:credits?|points?|tokens?)\b/i,
  /\bcredit\s+compensation\b/i,
  /(?:补发|补偿|退还|恢复).{0,12}?(?:积分|点数|额度)/u,
  /(?:积分|点数|额度).{0,12}?(?:补发|补偿|退还|恢复)/u,
  /(?:クレジット補填|ポイント返還)/u,
];

export function detectHumanActionRequirement(text: string): HumanActionDetection {
  if (!text || typeof text !== 'string') return { required: false };
  const trimmed = text.trim();
  if (!trimmed) return { required: false };
  if (REFUND_PATTERNS.some(r => r.test(trimmed))) return { required: true, category: 'refund' };
  if (BILLING_DISPUTE_PATTERNS.some(r => r.test(trimmed))) return { required: true, category: 'billing_dispute' };
  if (CREDIT_RESTORE_PATTERNS.some(r => r.test(trimmed))) return { required: true, category: 'credit_restore' };
  return { required: false };
}
