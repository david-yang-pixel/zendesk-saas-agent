import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
} from '@earendil-works/pi-coding-agent';
import { DIFY_SUPPORT_SYSTEM_PROMPT } from './agent.js';
import { createProjectSkillReadTool, getProjectSkills } from './project-skills.js';

const temporaryDirectories: string[] = [];
afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe('Pi project Skill loading', () => {
  it('advertises Skill metadata but loads its body only through read', async () => {
    const skills = getProjectSkills();
    const supportSkill = skills.find(skill => skill.name === 'dify-saas-support');
    expect(supportSkill).toBeDefined();
    const readTool = createProjectSkillReadTool(skills);
    const loader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: path.join(os.tmpdir(), 'pi-support-agent-test'),
      noContextFiles: true,
      skillsOverride: current => ({ skills, diagnostics: current.diagnostics }),
      systemPromptOverride: () => DIFY_SUPPORT_SYSTEM_PROMPT,
      appendSystemPromptOverride: () => [],
    });
    await loader.reload();
    const { session } = await createAgentSession({
      resourceLoader: loader,
      sessionManager: SessionManager.inMemory(),
      customTools: [readTool],
      tools: ['read'],
    });

    expect(session.systemPrompt).toContain('<name>dify-saas-support</name>');
    expect(session.systemPrompt).toContain(supportSkill!.filePath);
    expect(session.systemPrompt).not.toContain('# Dify SaaS Ticket Investigation and Reply');
    const registeredRead = (session as any)._toolRegistry.get('read');
    expect(registeredRead).toBeDefined();
    const result = await registeredRead.execute('read-skill', { path: supportSkill!.filePath });
    expect(result.content[0].text).toContain('# Dify SaaS Ticket Investigation and Reply');
    const referencedPaths = [...result.content[0].text.matchAll(/\]\((references\/[^)]+\.md)\)/g)]
      .map(match => path.resolve(supportSkill!.baseDir, match[1]));
    expect(referencedPaths.length).toBeGreaterThan(0);
    for (const referencePath of referencedPaths) {
      const reference = await registeredRead.execute('read-reference', { path: referencePath });
      expect(reference.content[0].text).toMatch(/^# /);
    }
    await expect(registeredRead.execute('read-outside', { path: path.resolve(process.cwd(), 'package.json') }))
      .rejects.toThrow(/outside project Skill/);
  });

  it('blocks reading non-Skill files and symlink escapes', async () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-skill-read-'));
    temporaryDirectories.push(temporaryDirectory);
    const skillDirectory = path.join(temporaryDirectory, 'skills', 'test-skill');
    fs.mkdirSync(skillDirectory, { recursive: true });
    fs.writeFileSync(path.join(skillDirectory, 'SKILL.md'), '---\nname: test-skill\ndescription: Test read boundary.\n---\n# Test\n');
    const skills = getProjectSkills(temporaryDirectory);
    const readTool = createProjectSkillReadTool(skills);
    const outsideFile = path.join(temporaryDirectory, 'outside.md');
    fs.writeFileSync(outsideFile, 'outside marker');
    await expect(readTool.execute('read-outside', { path: outsideFile }))
      .rejects.toThrow(/outside project Skill/);
    const link = path.join(skillDirectory, 'escape.md');
    fs.symlinkSync(outsideFile, link);
    await expect(readTool.execute('read-link', { path: link }))
      .rejects.toThrow(/outside project Skill/);
  });
});
