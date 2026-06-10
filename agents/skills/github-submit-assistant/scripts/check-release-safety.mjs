#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const root = process.argv[2] ? path.resolve(process.argv[2]) : process.cwd();
const fail = [];

const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');
const exists = (file) => fs.existsSync(path.join(root, file));
const tracked = execFileSync('git', ['ls-files', '-z'], { cwd: root })
  .toString('utf8')
  .split('\0')
  .filter(Boolean)
  .sort();

const requiredFiles = [
  'README.md',
  'NOTICE.md',
  'DISCLAIMER.md',
  'AGENT.md',
  'agents/README.md',
  'agents/skills/github-submit-assistant/SKILL.md',
  'agents/skills/github-submit-assistant/agents/openai.yaml',
  'agents/skills/github-submit-assistant/scripts/check-release-safety.mjs',
];

for (const file of requiredFiles) {
  if (!exists(file)) fail.push(`Missing required file: ${file}`);
}

const artifactPattern = /(^|\/)(data|logs|outputs|报销输出)(\/|$)|\.invoice_agent_ledger\.json$|config\.json$|\.(xlsx?|pdf|jpe?g|png|bmp|tiff?|webp)$/i;
for (const file of tracked) {
  if (artifactPattern.test(file)) {
    fail.push(`Tracked runtime/data artifact is not allowed: ${file}`);
  }
}

const textExtensions = new Set(['.md', '.py', '.js', '.json', '.html', '.css', '.sh', '.yaml', '.yml', '.txt']);
const privatePatterns = [
  { label: 'absolute macOS user path', pattern: /\/Users\/[A-Za-z0-9._-]+/ },
  { label: 'token environment assignment', pattern: /\b(OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN|LLM_API_KEY)=(?!\.\.\.)[^\s]+/i },
  { label: 'dated invoice folder name', pattern: /20\d{2}[^\n]{0,24}发票/ },
];

const outOfScopeFeatureTerms = [
  '工资',
  '课程',
  'salary',
  'payroll',
  'course management',
];

for (const file of tracked) {
  if (!textExtensions.has(path.extname(file))) continue;
  const text = read(file);
  for (const { label, pattern } of privatePatterns) {
    if (pattern.test(text)) {
      fail.push(`Forbidden private data pattern "${label}" found in ${file}`);
    }
  }
  const isPublicBoundaryDoc = file === 'NOTICE.md'
    || file === 'DISCLAIMER.md'
    || file === 'README.md'
    || file.startsWith('docs/updates/')
    || file.startsWith('agents/skills/github-submit-assistant/')
    || file === 'agents/README.md';
  if (!isPublicBoundaryDoc) {
    for (const term of outOfScopeFeatureTerms) {
      if (text.toLowerCase().includes(term.toLowerCase())) {
        fail.push(`Out-of-scope feature term "${term}" found in ${file}`);
      }
    }
  }
}

const bilingualTargets = [
  'README.md',
  'NOTICE.md',
  'DISCLAIMER.md',
  'agents/README.md',
  'agents/skills/github-submit-assistant/SKILL.md',
  ...tracked.filter((file) => file.startsWith('docs/updates/') && file.endsWith('.md')),
];

const hasChinese = (text) => /[\u3400-\u9fff]/.test(text);
const hasEnglishWord = (text) => /\b[A-Za-z][A-Za-z-]{2,}\b/.test(text);

for (const file of bilingualTargets) {
  if (!exists(file)) {
    fail.push(`Missing bilingual documentation target: ${file}`);
    continue;
  }
  const text = read(file);
  if (!hasChinese(text) || !hasEnglishWord(text)) {
    fail.push(`Bilingual documentation target lacks Chinese or English content: ${file}`);
  }
}

if (exists('README.md')) {
  const readme = read('README.md');
  for (const linked of ['NOTICE.md', 'DISCLAIMER.md', 'docs/updates/2026-06-10-public-release.md']) {
    if (!readme.includes(linked)) {
      fail.push(`README.md should link ${linked}`);
    }
  }
}

if (fail.length) {
  console.error('Release safety check failed:');
  for (const item of fail) console.error(`- ${item}`);
  process.exit(1);
}

console.log(`Release safety check passed: ${tracked.length} tracked file(s), ${bilingualTargets.length} bilingual documentation target(s).`);
