import fs from 'node:fs';
import path from 'node:path';
import {
  createReadToolDefinition,
  createSyntheticSourceInfo,
  type Skill,
} from '@earendil-works/pi-coding-agent';
import type { CustomTool } from './mcp.js';

function withinDirectory(filePath: string, directory: string): boolean {
  const relative = path.relative(directory, filePath);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

export function getProjectSkills(cwd = process.cwd()): Skill[] {
  const possiblePaths = [
    path.resolve(cwd, 'skills'),
    path.resolve(cwd, '../skills'),
    path.resolve(cwd, '../../skills'),
    '/app/skills',
  ];
  const skillsDir = possiblePaths.find(candidate => fs.existsSync(candidate));
  if (!skillsDir) return [];

  const root = fs.realpathSync(skillsDir);
  const projectSkills: Skill[] = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const skillPath = path.join(root, entry.name, 'SKILL.md');
    if (!fs.existsSync(skillPath) || !fs.lstatSync(skillPath).isFile()) continue;
    const canonicalPath = fs.realpathSync(skillPath);
    if (!withinDirectory(canonicalPath, root)) continue;
    const content = fs.readFileSync(canonicalPath, 'utf8');
    const matchName = content.match(/^name:\s*(.+)$/m);
    const matchDesc = content.match(/^description:\s*(.+)$/m);
    if (!matchName || !matchDesc) continue;
    projectSkills.push({
      name: matchName[1].trim(),
      description: matchDesc[1].trim(),
      filePath: canonicalPath,
      baseDir: path.dirname(canonicalPath),
      sourceInfo: createSyntheticSourceInfo(canonicalPath, { source: 'project' }),
      disableModelInvocation: false,
    });
  }
  return projectSkills;
}

/** Pi's native `read` tool, restricted to project Skill Markdown files. */
export function createProjectSkillReadTool(skills: readonly Skill[]): CustomTool {
  const allowedDirectories = skills.map(skill => fs.realpathSync(skill.baseDir));
  const checkedPath = async (filePath: string): Promise<string> => {
    const canonicalPath = await fs.promises.realpath(filePath);
    if (path.extname(canonicalPath).toLowerCase() !== '.md' ||
        !allowedDirectories.some(directory => withinDirectory(canonicalPath, directory))) {
      throw new Error('Reading outside project Skill Markdown files is not allowed');
    }
    return canonicalPath;
  };
  return createReadToolDefinition(process.cwd(), {
    operations: {
      access: async filePath => {
        await fs.promises.access(await checkedPath(filePath), fs.constants.R_OK);
      },
      readFile: async filePath => fs.promises.readFile(await checkedPath(filePath)),
    },
  }) as unknown as CustomTool;
}
