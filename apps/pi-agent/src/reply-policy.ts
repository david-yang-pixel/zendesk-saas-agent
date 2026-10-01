export type ReplyLanguage = 'en' | 'ja' | 'zh';

export function replyLanguage(text: string): ReplyLanguage {
  if (/[\u3040-\u30ff]/u.test(text) || /\b(?:in|speak|use) Japanese\b/i.test(text)) return 'ja';
  if (/[\u3400-\u9fff]/u.test(text) || /\b(?:in|speak|use) Chinese\b/i.test(text)) return 'zh';
  return 'en';
}

export function customerFooter(language: ReplyLanguage): string {
  const hours = 'Sunday–Thursday nights, 9:00 PM–6:00 AM EDT (daylight saving time) or 8:00 PM–5:00 AM EST (standard time)';
  const body = language === 'ja'
    ? `この返信は Dify AI Agent が送信しました。問題が解決しない場合は「human」と返信してください。サポート担当者が営業時間内に順次対応します（ニューヨーク時間：日曜～木曜、夏時間 21:00～翌6:00、標準時間 20:00～翌5:00）。`
    : language === 'zh'
      ? '此回复由 Dify AI Agent 发送。如果问题仍未解决，请回复“human”。人工支持会在服务时间内尽快回复（纽约时间：周日至周四晚间，夏令时 21:00 至次日 6:00，标准时间 20:00 至次日 5:00）。'
      : `This reply was sent by Dify AI Agent. If this does not resolve your issue, reply “human”. A human support engineer will respond as soon as possible during support hours (New York time): ${hours}.`;
  return `\n\n---\n${body}`;
}

/** Only complete conversational turns bypass investigation; mixed requests do not. */
export function conversationalReply(message: string): string | undefined {
  const text = message.trim();
  if (/^(?:hi(?: there)?|hello|hey|good (?:morning|afternoon|evening))[.!\s]*$/i.test(text)) return 'Hello! How can we help you?';
  if (/^(?:你好|您好)[！!。\s]*$/u.test(text)) return '您好！请告诉我们您需要什么帮助。';
  if (/^こんにちは[！!。\s]*$/u.test(text)) return 'こんにちは。どのようなことでお困りでしょうか。';
  if (/^(?:(?:perfect|great|ok(?:ay)?|got it|understood)[.!]?\s*)?(?:i should be able to work with that[.!]?\s*)?(?:thank(?:s| you)(?: very much)?|much appreciated)[.!\s]*$/i.test(text)) {
    return "You're welcome!";
  }
  if (/^(?:ありがとうございます|ありがとうございました|ありがとう|助かりました)[。！!\s]*$/u.test(text)) return 'どういたしまして。';
  if (/^(?:好的[，,。\s]*)?(?:谢谢|多谢|感谢)[你您！!。\s]*$/u.test(text)) return '不客气！';
  if (/^(?:日本語で(?:話せますか|話せます|対応できますか|相談できますか)|can (?:you|we) (?:speak|chat|talk)(?: in)? Japanese)[？?。.!\s]*$/iu.test(text)) {
    return 'はい、日本語でお問い合わせいただけます。どのようなことでお困りでしょうか。';
  }
  if (/^(?:可以(?:用)?中文(?:交流|沟通|回答)?吗|can (?:you|we) (?:speak|chat|talk)(?: in)? Chinese)[？?。.!\s]*$/iu.test(text)) {
    return '可以，请用中文告诉我们您需要什么帮助。';
  }
  return undefined;
}

export function replyReviewPrompt(evidence: string, publicReplyAllowed: boolean): string {
  return `Final customer reply quality review. Review and revise the latest draft against the prepared conversation and actual tool results. This is a review instruction, not customer content.
Return fresh === INTERNAL NOTE === and === CUSTOMER REPLY === sections; do not repeat an earlier draft without reviewing it.
The first line of INTERNAL NOTE must be one JSON object with exactly these review fields: {"approved":true,"claimMode":"qualified","answersLatestQuestion":true,"requestsOnlyMissingDetails":true,"usesApplicableEvidence":true}. Use claimMode "conversation" for acknowledgement/clarification without account or product claims, "qualified" for precise gaps/partial findings, or "verified" only when actual business reads support the factual answer. Set any check to false if it fails; approved may be true only when all checks pass. Put any factual internal notes after that JSON line.
Answer the latest actual question in its context. Do not turn a thank-you, greeting, or request to speak a language into account troubleshooting or a search for human staffing policy. ${publicReplyAllowed ? "Use the customer's requested language, otherwise the language of their latest substantive message." : 'The operator mention needs a customer-sendable English draft.'}
Use the known Dify Cloud web interface when that is the customer's interface; a CLI page alone does not answer a web-UI question. Do not ask again for details or attachments already present. An attached file that could not be read is an internal reading gap, not proof that the customer failed to attach it.
For every factual claim, verify source applicability and what the actual results prove. Discovery, snippets, empty results and failed calls do not establish an account fact or recovery. If no business read succeeded, preserve useful acknowledgements or clarification, but remove unverified factual claims. For partial findings, name the exact remaining gap and request only information the customer can supply; avoid a generic list of workspace ID/time/error ID. Explain feasible evidence-backed recovery steps when available. Do not claim a human handoff, fix, refund, activation or investigation occurred without an observed result.
Do not call tools merely to satisfy a check for a conversational reply. If the reply cannot be made useful and safe, explain the precise limitation in the reply. Never invent a conclusion.
Run evidence status:\n${evidence || 'No business evidence has been verified.'}`;
}

export function validateReplyReview(internalNote: string | undefined, hasBusinessEvidence: boolean): string | undefined {
  const [decisionLine, ...notes] = (internalNote || '').split('\n');
  let decision: any;
  try { decision = JSON.parse(decisionLine); }
  catch { throw new Error('Customer delivery blocked: reply quality review decision is missing'); }
  if (!decision || decision.approved !== true || decision.answersLatestQuestion !== true ||
      decision.requestsOnlyMissingDetails !== true || decision.usesApplicableEvidence !== true ||
      !['conversation', 'qualified', 'verified'].includes(decision.claimMode)) {
    throw new Error('Customer delivery blocked: reply quality review did not pass');
  }
  if (decision.claimMode === 'verified' && !hasBusinessEvidence) {
    throw new Error('Customer delivery blocked: verified reply has no successful business evidence');
  }
  return notes.join('\n').trim() || undefined;
}
