import { describe, expect, it } from 'vitest';
import { operatorRequestedPublicReply } from './mention-delivery.js';

describe('operator mention delivery authorization', () => {
  it('keeps an investigation request internal', () => {
    expect(operatorRequestedPublicReply('@David Yang 看下这个用户的教育优惠和积分为什么不刷新')).toBe(false);
  });

  it('requires an explicit instruction to reply to the customer', () => {
    expect(operatorRequestedPublicReply('@David Yang 请调查清楚后回复客户')).toBe(true);
    expect(operatorRequestedPublicReply('@agent Please reply to the customer after verification')).toBe(true);
  });

  it('honors an explicit no-public-reply instruction', () => {
    expect(operatorRequestedPublicReply('@David Yang 先查一下，不要回复客户')).toBe(false);
    expect(operatorRequestedPublicReply('@David Yang 请看一下为什么没有回复客户')).toBe(false);
    expect(operatorRequestedPublicReply('@David Yang 请只做内部调查，不回复客户')).toBe(false);
    expect(operatorRequestedPublicReply('@agent Internal only; do not send a public reply')).toBe(false);
  });
});
