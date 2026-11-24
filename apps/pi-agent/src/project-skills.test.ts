import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager,
} from '@earendil-works/pi-coding-agent';
import { supportPromptResources } from './agent.js';
import { createProjectSkillReadTool, getProjectSkills, inlineSkillBody } from './project-skills.js';

const temporaryDirectories: string[] = [];
afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe('Pi project Skill loading', () => {
  it('puts the support Skill body in the system prompt while other Skills and references stay on demand', async () => {
    const skills = getProjectSkills();
    const readTool = createProjectSkillReadTool(skills);
    const { systemPrompt, advertisedSkills } = supportPromptResources(skills, 'DIRECTORY-SNAPSHOT');
    const loader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: path.join(os.tmpdir(), 'pi-support-agent-test'),
      noContextFiles: true,
      skillsOverride: current => ({ skills: advertisedSkills, diagnostics: current.diagnostics }),
      systemPromptOverride: () => systemPrompt,
      appendSystemPromptOverride: () => [],
    });
    await loader.reload();
    const { session } = await createAgentSession({
      resourceLoader: loader,
      sessionManager: SessionManager.inMemory(),
      customTools: [readTool],
      tools: ['read'],
    });

    expect(session.systemPrompt).toContain('# Dify SaaS Ticket Investigation and Reply');
    expect(session.systemPrompt).not.toContain('name: dify-saas-support');
    expect(session.systemPrompt).not.toContain('<name>dify-saas-support</name>');
    expect(session.systemPrompt).toContain('<name>openconnector-routing</name>');
    expect(session.systemPrompt).not.toContain('# OpenConnector Investigation Routing');
    expect(session.systemPrompt).toContain('DIRECTORY-SNAPSHOT');
    const registeredRead = (session as any)._toolRegistry.get('read');
    const referencedPaths = [...session.systemPrompt.matchAll(/\]\(([^)]*references\/[^)]+\.md)\)/g)].map(match => match[1]);
    expect(referencedPaths.length).toBeGreaterThan(0);
    for (const referencePath of referencedPaths) {
      expect(path.isAbsolute(referencePath)).toBe(true);
      const reference = await registeredRead.execute('read-reference', { path: referencePath });
      expect(reference.content[0].text).toMatch(/^# /);
    }
    await expect(registeredRead.execute('read-outside', { path: path.resolve(process.cwd(), 'package.json') }))
      .rejects.toThrow(/outside project Skill/);
  });

  it('inlines a Skill without its frontmatter and resolves only relative Markdown links', () => {
    const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-skill-inline-'));
    temporaryDirectories.push(temporaryDirectory);
    const skillDirectory = path.join(temporaryDirectory, 'skills', 'inline-skill');
    fs.mkdirSync(skillDirectory, { recursive: true });
    fs.writeFileSync(path.join(skillDirectory, 'SKILL.md'), [
      '---', 'name: inline-skill', 'description: Inline test.', '---', '',
      '# Inline Skill', '',
      'See [Billing](references/billing.md), [Guide](./guide.md#limits), [Docs](https://docs.example/a.md) and [Top](#scope).',
    ].join('\n'));
    const [skill] = getProjectSkills(temporaryDirectory);
    const realDirectory = fs.realpathSync(skillDirectory);

    expect(inlineSkillBody(skill)).toBe([
      '# Inline Skill', '',
      `See [Billing](${path.join(realDirectory, 'references/billing.md')}), [Guide](${path.join(realDirectory, 'guide.md')}#limits), [Docs](https://docs.example/a.md) and [Top](#scope).`,
    ].join('\n'));
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
