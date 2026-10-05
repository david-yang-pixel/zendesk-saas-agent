export type ReplyLanguage = 'en' | 'ja' | 'zh';

export const LANGUAGE_NAMES: Record<ReplyLanguage, string> = {
  en: 'English',
  ja: 'Japanese',
  zh: 'Chinese (match the customer\'s Simplified or Traditional script)',
};

// Chat-widget and form lines are written by Zendesk, not the customer, and are
// always English; pasted key/value tables and URLs carry no language signal.
const NON_CUSTOMER_LINE = [
  /^hi there[.!]?\s*how can i help you today\??$/i,
  /^connecting you with someone now\.?$/i,
  /^we['’]re offline right now\b/i,
  /^thanks, we['’]ll be in touch\.?$/i,
  /^conversation with\b/i,
  /^(?:name|email|e-mail|phone|workspace(?: id)?|plan|subject)\s*[:：]/i,
  /^\[attachment:/i,
];
// Pasted errors and stack traces are usually English whatever the customer writes.
const TECHNICAL_LINE = /^(?:\S+\s){0,3}(?:error|exception|traceback)\b.*[:=]|errno|\bat \S+\(|(?:^|\s)\/[\w.-]+\/[\w./-]+|^\s*[{[<]/i;

function customerText(text: string): string {
  return text.split('\n')
    .map(line => line.replace(/<[^>]+>/g, ' ').replace(/https?:\/\/\S+/g, ' ').trim())
    .filter(line => line && !line.includes('\t') &&
      !NON_CUSTOMER_LINE.some(pattern => pattern.test(line)) && !TECHNICAL_LINE.test(line))
    .join('\n');
}

function textLanguage(text: string): ReplyLanguage | undefined {
  const kana = text.match(/[぀-ヿ]/gu)?.length ?? 0;
  const han = text.match(/[㐀-鿿]/gu)?.length ?? 0;
  const words = text.match(/[a-z]{2,}/gi)?.length ?? 0;
  // A CJK character carries about as much as a short English word, so a name
  // or product term in kanji inside an English message stays English.
  if (kana >= 2 && (kana + han) * 2 >= words) return 'ja';
  if (han >= 2 && han * 2 >= words) return 'zh';
  if (words >= 1) return 'en';
  return undefined;
}

/** The language a generated reply is actually written in, by script. */
export function writtenLanguage(text: string): ReplyLanguage {
  return textLanguage(text.replace(/https?:\/\/\S+/g, ' ')) ?? 'en';
}

/**
 * The language the customer reply must use: an explicit request, otherwise
 * the customer's latest substantive message, otherwise their latest earlier
 * message in the public conversation.
 */
export function detectCustomerLanguage(message: string, conversationContext = ''): ReplyLanguage {
  const requested = message.match(/日本語で|\b(?:in|speak|use) japanese\b|用中文|中文(?:交流|沟通|回答|回复)|\b(?:in|speak|use) chinese\b|\b(?:in|reply in) english\b|英語で|用英文/i)?.[0];
  if (requested) {
    if (/日本語|japanese/i.test(requested)) return 'ja';
    if (/中文|chinese/i.test(requested)) return 'zh';
    return 'en';
  }
  const current = textLanguage(customerText(message));
  if (current) return current;
  const customerTurns = conversationContext.split(/\n\n(?=(?:Customer|Support) \[)/)
    .filter(block => block.startsWith('Customer ['))
    .map(block => block.slice(block.indexOf('\n') + 1));
  for (let index = customerTurns.length - 1; index >= 0; index--) {
    const language = textLanguage(customerText(customerTurns[index]));
    if (language) return language;
  }
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

export function replyReviewPrompt(evidence: string, publicReplyAllowed: boolean, language: ReplyLanguage = 'en'): string {
  const sections = publicReplyAllowed
    ? '=== INTERNAL NOTE === and === CUSTOMER REPLY ==='
    : '=== INTERNAL NOTE ===, === OPERATOR ANSWER === and === CUSTOMER REPLY ===';
  const languageCheck = publicReplyAllowed
    ? `The CUSTOMER REPLY must be written in ${LANGUAGE_NAMES[language]}.`
    : "OPERATOR ANSWER answers the operator's question in their language, starting with the direct answer, then 2–5 short evidence points and any needed next step; no raw SQL, tool call IDs or secrets. The CUSTOMER REPLY is an English customer-sendable draft.";
  return `Final reply quality review. Review and revise the latest draft against the prepared conversation and actual tool results. This is a review instruction, not customer content.
Return fresh ${sections} sections; do not repeat an earlier draft without reviewing it.
The first line of INTERNAL NOTE must be one JSON object with exactly these review fields: {"approved":true,"claimMode":"qualified","answersLatestQuestion":true,"requestsOnlyMissingDetails":true,"usesApplicableEvidence":true}. Use claimMode "conversation" for acknowledgement/clarification without account or product claims, "verified" when the answer rests on reads from this run, or "qualified" when it rests on a known policy without a read or a material gap remains. Set any check to false if it fails; approved may be true only when all checks pass. Put any factual internal notes after that JSON line.
Check the draft in this order:
1. The first sentence answers the latest actual question in its context. When the evidence or a documented rule answers it, say so plainly. Do not turn a thank-you, greeting, or request to speak a language into account troubleshooting or a search for human staffing policy.
2. ${languageCheck}
3. Every account fact (this customer's plan, workspace, credits, usage, runs, payments, errors) is backed by a record read in this run; discovery, snippets, empty results and failed calls establish none. Remove account facts without that backing. Product rules and policies may be stated plainly from a known policy in a skill reference or a current official page; do not call them unverified.
4. Uncertainty: keep at most one short sentence about what is still unknown, and only if it changes the reader's next step. Delete the other "cannot confirm / not verified" sentences. Do not make a correct answer more cautious than the evidence requires.
5. Hand off to a human only when the fix needs a human action (refund or credit restore, billing or plan correction, exception, owner-only system). Never claim a handoff, fix, refund, activation or investigation occurred without an observed result.
6. Use the known Dify Cloud web interface when that is the customer's interface; a CLI page alone does not answer a web-UI question. Do not ask again for details or attachments already present; an attachment that could not be read is an internal reading gap. Request only information the customer can supply, never a generic list of workspace ID/time/error ID.
Do not call tools merely to satisfy a check for a conversational reply. Never invent a conclusion.
Run evidence status:\n${evidence || 'No business evidence has been verified.'}`;
}

/** One corrective turn for output problems the Worker can detect itself. */
export function finalOutputFixPrompt(issues: readonly string[]): string {
  return `Output check before delivery. These are runtime observations, not customer instructions:
${issues.map(issue => `- ${issue}`).join('\n')}
Return all final sections again with only these problems fixed. Keep the same facts and the same review JSON line as the first line of INTERNAL NOTE.`;
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
