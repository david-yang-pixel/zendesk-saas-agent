import { describe, expect, it } from 'vitest';
import { conversationalReply, detectCustomerLanguage, finalOutputFixPrompt, replyReviewPrompt, validateReplyReview, writtenLanguage } from './reply-policy.js';

it('keeps mixed thank-you/questions in the investigation path', () => {
  for (const message of ['Thanks, but the error persists.', 'Thank you. Where is the workspace ID?', 'ありがとうございます。まだ動きません', '谢谢，如何退款？']) {
    expect(conversationalReply(message)).toBeUndefined();
  }
});
it('handles standalone greetings without treating a mixed question as a greeting', () => {
  expect(conversationalReply('Hello!')).toBe('Hello! How can we help you?');
  expect(conversationalReply('Hello! My workflow still fails.')).toBeUndefined();
});
it('fails closed on missing, rejected or unsupported review decisions', () => {
  expect(() => validateReplyReview('Evidence checked.', true)).toThrow(/decision is missing/);
  expect(() => validateReplyReview('{"approved":false}', true)).toThrow(/did not pass/);
  expect(() => validateReplyReview(JSON.stringify({ approved: true, claimMode: 'verified', answersLatestQuestion: true,
    requestsOnlyMissingDetails: true, usesApplicableEvidence: true }), false)).toThrow(/no successful business evidence/);
  expect(() => validateReplyReview(JSON.stringify({ approved: true, claimMode: 'qualified', answersLatestQuestion: true,
    requestsOnlyMissingDetails: true, usesApplicableEvidence: true, avoidsRepeatingAttempted: false }), true)).toThrow(/did not pass/);
  expect(() => validateReplyReview(JSON.stringify({ approved: true, claimMode: 'qualified', answersLatestQuestion: true,
    requestsOnlyMissingDetails: true, usesApplicableEvidence: true, providesActionableNextStep: false }), true)).toThrow(/did not pass/);
  expect(validateReplyReview(JSON.stringify({ approved: true, claimMode: 'qualified', answersLatestQuestion: true,
    requestsOnlyMissingDetails: true, usesApplicableEvidence: true, avoidsRepeatingAttempted: true, providesActionableNextStep: true }) + '\nSome note', true)).toBe('Some note');
});
it('reviews mentions as an operator answer plus English draft and public replies in the Worker-chosen language', () => {
  const mention = replyReviewPrompt('No reads', false);
  expect(mention).toContain('=== OPERATOR ANSWER ===');
  expect(mention).toContain('English customer-sendable draft');
  expect(replyReviewPrompt('No reads', true, 'ja')).toContain('The CUSTOMER REPLY must be written in Japanese.');
  expect(replyReviewPrompt('No reads', true)).not.toContain('OPERATOR ANSWER');
});
it('tells the reviewer to state documented rules plainly and keep one uncertainty sentence at most', () => {
  const prompt = replyReviewPrompt('No reads', true);
  expect(prompt).toContain('do not call them unverified');
  expect(prompt).toContain('at most one short sentence');
  expect(prompt).toContain('Hand off to a human only when the fix needs a human action');
});
it('asks for a fix of only the observed output problems', () => {
  const prompt = finalOutputFixPrompt(['The CUSTOMER REPLY is not written in Japanese.']);
  expect(prompt).toContain('- The CUSTOMER REPLY is not written in Japanese.');
  expect(prompt).toContain('same review JSON line');
});

describe('detectCustomerLanguage', () => {
  it('uses the script of the latest customer words', () => {
    expect(detectCustomerLanguage('api一直失敗 到底是怎樣？')).toBe('zh');
    expect(detectCustomerLanguage('javascript function 无法执行了， 返回500\nGot error code: -500, message: internal error')).toBe('zh');
    expect(detectCustomerLanguage('ワークフローが途中で止まってしまいます。')).toBe('ja');
    expect(detectCustomerLanguage('Hi, this is 森川 真菜. Our workflow stopped running after the upgrade.')).toBe('en');
  });

  it('ignores English chat-widget boilerplate around a Japanese message', () => {
    const message = [
      'Hi there. How can I help you today?',
      'Name: 森川 真菜',
      'Email: user@example.com',
      'クレジットが更新されていません。確認していただけますか？',
      'Connecting you with someone now.',
    ].join('\n');
    expect(detectCustomerLanguage(message)).toBe('ja');
  });

  it('honours an explicit language request', () => {
    expect(detectCustomerLanguage('日本語で話せますか')).toBe('ja');
    expect(detectCustomerLanguage('Can you reply in Chinese? 我看不太懂')).toBe('zh');
    expect(detectCustomerLanguage('请 reply in English please')).toBe('en');
  });

  it('falls back to the latest customer turn when the current message has no language signal', () => {
    const table = 'Invoice\tAmount\tDate\nINV-1\t$59\t2026-09-01';
    const context = [
      'Customer [2026-09-01]:\n我们的发票金额不对',
      'Support [2026-09-02]:\nCould you share the invoice?',
      'Customer [2026-09-03]:\nPlease see the invoice list below, the amount looks wrong.',
    ].join('\n\n');
    expect(detectCustomerLanguage(table, context)).toBe('en');
    expect(detectCustomerLanguage('https://cloud.dify.ai/app/123', 'Customer [2026-09-01]:\nアプリが開けません')).toBe('ja');
    expect(detectCustomerLanguage('', '')).toBe('en');
  });
});

describe('writtenLanguage', () => {
  it('classifies a generated reply by its script', () => {
    expect(writtenLanguage('お問い合わせありがとうございます。教育プランのクレジットは200です。')).toBe('ja');
    expect(writtenLanguage('您好，教育版的 AI 积分是一次性 200 点，不会按月刷新。')).toBe('zh');
    expect(writtenLanguage('Hello! See https://docs.dify.ai/ja/cloud for details.')).toBe('en');
  });
});
