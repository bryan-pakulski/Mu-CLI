# UX Design Guidelines

## Minimalist container rule (new)

- **Do not put containers inside containers.**
- Keep at most **one visible container layer** per region.
- Prefer spacing, typography, and subtle dividers over stacked cards/boxes.
- Avoid default AI-looking nested cards, rounded boxes, and framed sections.

## Practical checks

Before shipping UI changes, verify:

1. A panel does not contain another bordered panel/card unless functionally required.
2. Modal content is mostly flat layout (headings, rows, tables) instead of nested boxed blocks.
3. Timeline/diff/review areas use one primary container and lightweight rows.
4. Empty/loading/error states are legible without creating extra nested boxes.

## Direction

- Style should feel calm, editorial, and utility-first.
- Use hierarchy through type/spacing, not heavy visual chrome.
