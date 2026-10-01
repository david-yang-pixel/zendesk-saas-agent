import { expect, it } from 'vitest';
import { conversationalReply, replyReviewPrompt, validateReplyReview } from './reply-policy.js';

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
});
it('keeps operator review drafts English while customer review uses the requested language', () => {
  expect(replyReviewPrompt('No reads', false)).toContain('English draft');
  expect(replyReviewPrompt('No reads', true)).toContain("customer's requested language");
});
