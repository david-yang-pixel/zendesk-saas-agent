import { describe, it, expect } from 'vitest';
import { detectHumanHandoffIntent } from './intent.js';

describe('detectHumanHandoffIntent', () => {
  describe('English handoff requests', () => {
    it('detects explicit requests to talk to human', () => {
      expect(detectHumanHandoffIntent('I want to talk to a human')).toBe(true);
      expect(detectHumanHandoffIntent('Please transfer me to an agent')).toBe(true);
      expect(detectHumanHandoffIntent('Can I speak with a real person?')).toBe(true);
      expect(detectHumanHandoffIntent('Connect me to live support')).toBe(true);
      expect(detectHumanHandoffIntent('I need a human agent now')).toBe(true);
      expect(detectHumanHandoffIntent('Human support please')).toBe(true);
      expect(detectHumanHandoffIntent('Real person please')).toBe(true);
    });

    it('detects standalone keywords and short commands', () => {
      expect(detectHumanHandoffIntent('support')).toBe(true);
      expect(detectHumanHandoffIntent('Support')).toBe(true);
      expect(detectHumanHandoffIntent('support!')).toBe(true);
      expect(detectHumanHandoffIntent('human')).toBe(true);
      expect(detectHumanHandoffIntent('agent')).toBe(true);
      expect(detectHumanHandoffIntent('live agent')).toBe(true);
      expect(detectHumanHandoffIntent('human please')).toBe(true);
      expect(detectHumanHandoffIntent('please support')).toBe(true);
      expect(detectHumanHandoffIntent('Are you a human?')).toBe(true);
      expect(detectHumanHandoffIntent('Is this a bot?')).toBe(true);
    });

    it('does NOT trigger on technical or product inquiries containing keywords', () => {
      expect(detectHumanHandoffIntent('How do I configure a human-in-the-loop workflow in Dify?')).toBe(false);
      expect(detectHumanHandoffIntent('We are testing human-eval benchmarks with our dataset')).toBe(false);
      expect(detectHumanHandoffIntent('Does Dify support RLHF or human feedback nodes?')).toBe(false);
      expect(detectHumanHandoffIntent('How to integrate Dify with our customer service system?')).toBe(false);
      expect(detectHumanHandoffIntent('Can we build a customer service bot with Dify?')).toBe(false);
      expect(detectHumanHandoffIntent('We need to evaluate automated customer service')).toBe(false);
      expect(detectHumanHandoffIntent('What are the differences between human vs AI agents?')).toBe(false);
    });
  });

  describe('Chinese handoff requests', () => {
    it('detects explicit requests for human agent', () => {
      expect(detectHumanHandoffIntent('转人工')).toBe(true);
      expect(detectHumanHandoffIntent('人工客服')).toBe(true);
      expect(detectHumanHandoffIntent('人工')).toBe(true);
      expect(detectHumanHandoffIntent('客服')).toBe(true);
      expect(detectHumanHandoffIntent('请帮我转人工服务')).toBe(true);
      expect(detectHumanHandoffIntent('需要真人客服协助一下')).toBe(true);
      expect(detectHumanHandoffIntent('呼叫人工客服')).toBe(true);
      expect(detectHumanHandoffIntent('你们有人工客服在吗？')).toBe(true);
      expect(detectHumanHandoffIntent('我要找人工')).toBe(true);
      expect(detectHumanHandoffIntent('转人工客服解决一下')).toBe(true);
      expect(detectHumanHandoffIntent('是不是人工？')).toBe(true);
      expect(detectHumanHandoffIntent('是人工吗')).toBe(true);
      expect(detectHumanHandoffIntent('有人在吗')).toBe(true);
    });

    it('does NOT trigger on technical inquiries with keywords', () => {
      expect(detectHumanHandoffIntent('请问工作流里如何配置人机协同节点？')).toBe(false);
      expect(detectHumanHandoffIntent('我们的业务需要人工审核流程，Dify怎么实现？')).toBe(false);
      expect(detectHumanHandoffIntent('想用 Dify 搭建一个智能客服机器人')).toBe(false);
      expect(detectHumanHandoffIntent('怎么把这个应用对接外部客服系统？')).toBe(false);
      expect(detectHumanHandoffIntent('是否有客服接口的 API 文档？')).toBe(false);
    });
  });

  describe('Japanese handoff requests', () => {
    it('detects Japanese operator requests', () => {
      expect(detectHumanHandoffIntent('オペレーターに繋いでください')).toBe(true);
      expect(detectHumanHandoffIntent('担当者と話したいです')).toBe(true);
      expect(detectHumanHandoffIntent('人間と話せますか？')).toBe(true);
      expect(detectHumanHandoffIntent('オペレーター')).toBe(true);
      expect(detectHumanHandoffIntent('有人のサポート窓口に相談したい')).toBe(true);
    });

    it('does NOT trigger on technical Japanese inquiries', () => {
      expect(detectHumanHandoffIntent('カスタマーサポートボットの作成方法を教えてください')).toBe(false);
      expect(detectHumanHandoffIntent('外部カスタマーサポートシステム連携について')).toBe(false);
    });
  });

  describe('Edge cases', () => {
    it('handles empty or blank input gracefully', () => {
      expect(detectHumanHandoffIntent('')).toBe(false);
      expect(detectHumanHandoffIntent('   ')).toBe(false);
      expect(detectHumanHandoffIntent(null as any)).toBe(false);
      expect(detectHumanHandoffIntent(undefined as any)).toBe(false);
    });
  });
});
