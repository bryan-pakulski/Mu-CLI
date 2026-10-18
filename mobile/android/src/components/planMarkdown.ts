
export type PlanRow = { key: string; text: string };
export type PlanSection = 'Acceptance criteria' | 'Checks';
export const PLAN_SECTIONS: PlanSection[] = ['Acceptance criteria', 'Checks'];

export function sectionPattern(title: PlanSection) {
  return new RegExp(`(^## ${title}[^\\S\\n]*\\n)([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'im');
}

export function rowsFrom(markdown: string, title: PlanSection): PlanRow[] {
  const body = sectionPattern(title).exec(markdown)?.[2] || '';
  return body.split('\n').filter(line => line.trim()).map((line, index) => {
    const match = title === 'Checks'
      ? /^\s*[-*]\s*suite:\s*(\S*)\s+cmd: ?(.*)$/.exec(line)
      : /^\s*[-*]\s*\[([^\]]*)\] ?(.*)$/.exec(line);
    return match ? { key: match[1], text: match[2] } : {
      key: title === 'Checks' ? `check${index + 1}` : `AC-${index + 1}`,
      text: line.replace(/^\s*(?:[-*]|\d+[.)])\s+/, ''),
    };
  });
}

export function replaceRows(markdown: string, title: PlanSection, rows: PlanRow[]) {
  const body = rows.map(row => title === 'Checks'
    ? `- suite: ${row.key}   cmd: ${row.text}` : `- [${row.key}] ${row.text}`).join('\n');
  const section = `## ${title}\n${body}\n\n`;
  return sectionPattern(title).test(markdown)
    ? markdown.replace(sectionPattern(title), () => section)
    : `${markdown.trimEnd()}\n\n${section}`;
}

/** Plan prose without front matter or the structured sections shown as cards. */
export function planProse(markdown: string): string {
  let text = markdown.replace(/^---\n[\s\S]*?\n---\n?/, '');
  for (const title of PLAN_SECTIONS) text = text.replace(sectionPattern(title), '');
  return text.trim();
}

// Markdown styles for job documents live in the shared module: src/ui/Markdown.tsx.
