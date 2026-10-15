// Wash iconography contract (G§4, spec §5.2): one line-icon set rendered by
// the kit `Icon`, every name exists in the generated data, and sizes come
// only from tokens.

import * as fs from 'fs';
import * as path from 'path';
import { iconData } from '../src/components/icons/iconData.generated';

const SRC = path.join(__dirname, '..', 'src');

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

const files = sourceFiles(SRC).map(file => ({ file: path.relative(SRC, file), text: fs.readFileSync(file, 'utf8') }));

/** The text of a JSX attribute value starting at `start` (a quote or `{`). */
function attributeValue(text: string, start: number): string {
  if (text[start] !== '{') {
    const end = text.indexOf(text[start], start + 1);
    return text.slice(start, end + 1);
  }
  let depth = 0;
  for (let i = start; i < text.length; i += 1) {
    if (text[i] === '{') depth += 1;
    if (text[i] === '}') {
      depth -= 1;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return text.slice(start);
}

/** String literals in an attribute value, skipping comparison operands (`mode === 'form' ? …`). */
function literals(value: string): string[] {
  return Array.from(value.matchAll(/(?<!(?:===|!==|==|!=)\s*)(['"])([a-z][a-z0-9-]*)\1/g), m => m[2]);
}

/** Every literal icon name used in src: Icon/IconButton props and `icon` fields. */
function usedIconNames(): Array<{ file: string; name: string }> {
  const out: Array<{ file: string; name: string }> = [];
  for (const { file, text } of files) {
    for (const tag of text.matchAll(/<(Icon|IconButton)\b/g)) {
      const attr = tag[1] === 'Icon' ? 'name' : 'icon';
      const rest = text.slice(tag.index!);
      const m = rest.match(new RegExp(`\\s${attr}=(["{])`));
      if (!m || m.index! > rest.indexOf('>')) continue;
      literals(attributeValue(rest, m.index! + m[0].length - 1)).forEach(name => out.push({ file, name }));
    }
    for (const m of text.matchAll(/\bicon(?:=|:\s*)(['"])([a-z][a-z0-9-]*)\1/g)) out.push({ file, name: m[2] });
  }
  return out;
}

describe('Wash icons', () => {
  it('no file under src/ imports @expo/vector-icons or uses Ionicons', () => {
    const offenders = files.filter(({ text }) => /@expo\/vector-icons|\bIonicons\b/.test(text)).map(f => f.file);
    expect(offenders).toEqual([]);
  });

  it('finds icon usages to check', () => {
    expect(usedIconNames().length).toBeGreaterThan(100);
  });

  it('every Icon name used in src/ exists in iconData', () => {
    const missing = usedIconNames().filter(({ name }) => !(name in iconData));
    expect(missing).toEqual([]);
  });

  it('no emoji in product chrome (status glyphs like ✓ ✗ ◆ ● stay)', () => {
    const offenders: string[] = [];
    for (const { file, text } of files) {
      text.split('\n').forEach((line, i) => {
        if (/\p{Emoji_Presentation}|️/u.test(line)) offenders.push(`${file}:${i + 1}`);
      });
    }
    expect(offenders).toEqual([]);
  });

  it('Icon sizes come only from tokens (sm/md/lg)', () => {
    const numeric: string[] = [];
    for (const { file, text } of files) {
      for (const tag of text.matchAll(/<Icon\b/g)) {
        const rest = text.slice(tag.index!);
        const m = rest.match(/\ssize=(["{])/);
        if (!m || m.index! > rest.indexOf('/>')) continue;
        const value = attributeValue(rest, m.index! + m[0].length - 1);
        const tokensOnly = !/\d/.test(value) && literals(value).every(v => ['sm', 'md', 'lg'].includes(v));
        if (!tokensOnly) numeric.push(`${file}: size=${value}`);
      }
    }
    expect(numeric).toEqual([]);
  });
});
