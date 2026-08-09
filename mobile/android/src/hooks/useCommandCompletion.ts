import { useState, useCallback, useRef, useEffect } from 'react';
import { chatApi, type CommandSpec } from '../api/chat';

// ── Types ────────────────────────────────────────────────────────────

export interface CompletionItem {
  label: string;
  desc: string;
  value: string;
  level: number;
  isPath?: boolean;
}

interface SubTree {
  subs?: string[];
  nested?: Record<string, SubTree>;
  dynamic?: Record<string, string>;
}

// Static subcommand tree — mirrors the web GUI's Alpine.store("cmdComplete")._subTree
// exactly. Dynamic completions (sessions, features, tools, etc.) are fetched
// from /api/chat/completions at runtime.
const SUB_TREE: Record<string, SubTree> = {
  '/history':       { subs: ['clear', 'show'] },
  '/session':       { subs: ['list', 'load', 'new', 'delete'],
                      nested: { new: { subs: ['--type'], nested: { '--type': { subs: ['chat', 'workspace', 'container'] } } } },
                      dynamic: { load: 'sessions', delete: 'sessions' } },
  '/workspace':     { subs: ['folder', 'file', 'clear'],
                      nested: {
                        folder: { subs: ['remove', 'clear'],
                                  dynamic: { remove: 'path_dir', '': 'path_dir' } },
                        file:   { subs: ['clear'],
                                  dynamic: { '': 'path_file' } },
                      } },
  '/model':         { dynamic: { '': 'models' } },
  '/provider':      { subs: ['gemini', 'ollama', 'openai'] },
  '/ollama':        { subs: ['status', 'models', 'options', 'pull'] },
  '/set':           { subs: ['layer'], dynamic: { '': 'variables', layer: 'layer_ids' } },
  '/get':           { dynamic: { '': 'variables', layer: 'layer_ids' },
                      subs: ['layer'] },
  '/unset':         { dynamic: { '': 'variables' }, subs: ['--all'] },
  '/mode':          { dynamic: { '': 'modes' } },
  '/plan':          { subs: ['on', 'off', 'toggle'] },
  '/verbose':       { subs: ['on', 'off', 'toggle'] },
  '/show-thinking': { subs: ['on', 'off', 'toggle'] },
  '/goal':          { subs: ['set', 'clear', 'show', 'help'] },
  '/research':      { subs: ['status', 'sources', 'show', 'bibliography',
                             'biblio', 'bib', 'stats', 'clear'] },
  '/memory':        { subs: ['status', 'list', 'clear'],
                      nested: {
                        list:  { dynamic: { '': 'memory_targets' } },
                        clear: { subs: ['task', 'scratchpad', 'all'] },
                      } },
  '/tool':          { subs: ['list', 'enable', 'disable'],
                      dynamic: { enable: 'tools', disable: 'tools' } },
  '/feature':       { subs: ['list', 'show', 'new', 'load', 'delete',
                             'status', 'phases', 'exit', 'unload'],
                      dynamic: { load: 'features', delete: 'features',
                                 status: 'features', phases: 'features' } },
  '/teach':         { subs: ['list', 'new', 'load', 'delete', 'exit',
                             'unload', 'status', 'next', 'grades',
                             'curriculum', 'help'] },
  '/t':             { subs: ['list', 'new', 'load', 'delete', 'exit',
                             'unload', 'status', 'next', 'grades',
                             'curriculum', 'help'] },
  '/stats':         { subs: ['clear'] },
  '/skills':        { dynamic: { '': 'skills' } },
  '/docs':          { dynamic: { '': 'docs' } },
};

// ── Hook ──────────────────────────────────────────────────────────────

export function useCommandCompletion() {
  const [commands, setCommands] = useState<CommandSpec[]>([]);
  const [visible, setVisible] = useState(false);
  const [items, setItems] = useState<CompletionItem[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const dynCacheRef = useRef<Record<string, string[]>>({});
  const loadedRef = useRef(false);

  // Load command list once on mount (mirrors cmdComplete.load()).
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    chatApi.getCommands()
      .then(data => setCommands(data.commands || []))
      .catch(() => { /* silent — completion just won't show command names */ });
  }, []);

  const close = useCallback(() => {
    setVisible(false);
    setItems([]);
    setSelectedIdx(0);
  }, []);

  const moveUp = useCallback(() => {
    setItems(prev => {
      if (prev.length) setSelectedIdx(i => (i - 1 + prev.length) % prev.length);
      return prev;
    });
  }, []);

  const moveDown = useCallback(() => {
    setItems(prev => {
      if (prev.length) setSelectedIdx(i => (i + 1) % prev.length);
      return prev;
    });
  }, []);

  const invalidateCache = useCallback((kind?: string) => {
    if (kind) delete dynCacheRef.current[kind];
    else dynCacheRef.current = {};
  }, []);

  // Fetch dynamic completion list from server, with caching.
  const fetchDynamic = useCallback(async (kind: string): Promise<string[] | null> => {
    if (kind.startsWith('path_')) return null;
    if (dynCacheRef.current[kind]) return dynCacheRef.current[kind];
    try {
      const data = await chatApi.getCompletions(kind);
      dynCacheRef.current[kind] = data.items || [];
      return dynCacheRef.current[kind];
    } catch {
      return [];
    }
  }, []);

  // Add dynamic items to the results array.
  const addDynItems = useCallback(async (
    result: CompletionItem[],
    kind: string,
    query: string,
    prefix: string,
    level: number,
  ) => {
    // Path completions are not supported on mobile (no filesystem browse endpoint
    // wired). Skip gracefully — the user can still type the path manually.
    if (kind.startsWith('path_')) return;
    const dynItems = await fetchDynamic(kind);
    if (dynItems) {
      for (const d of dynItems) {
        if (d.toLowerCase().startsWith(query)) {
          result.push({ label: d, desc: '', value: prefix + d, level });
        }
      }
    }
  }, [fetchDynamic]);

  // Main completion logic — mirrors Alpine.store("cmdComplete").update().
  const update = useCallback(async (text: string) => {
    if (!text.startsWith('/')) { close(); return; }

    const parts = text.split(/\s+/);
    const cmd = parts[0];

    // Level 0: completing the command name itself (no space typed yet)
    if (parts.length === 1) {
      const q = cmd.toLowerCase();
      const matched: CompletionItem[] = commands
        .filter(c => c.names[0].toLowerCase().startsWith(q) ||
                     c.names.slice(1).some(a => a.toLowerCase().startsWith(q)))
        .map(c => ({ label: c.names[0], desc: c.help, value: c.names[0], level: 0 }));
      setSelectedIdx(0);
      setItems(matched);
      setVisible(matched.length > 0);
      return;
    }

    const tree = SUB_TREE[cmd];
    if (!tree) { close(); return; }

    // Level 1: completing the subcommand or first arg
    if (parts.length === 2) {
      const q = parts[1].toLowerCase();
      const result: CompletionItem[] = [];

      if (tree.subs) {
        for (const s of tree.subs) {
          if (s.toLowerCase().startsWith(q)) {
            result.push({ label: s, desc: '', value: cmd + ' ' + s, level: 1 });
          }
        }
      }

      if (tree.dynamic && tree.dynamic[''] !== undefined) {
        await addDynItems(result, tree.dynamic[''], q, cmd + ' ', 1);
      }

      setSelectedIdx(0);
      setItems(result);
      setVisible(result.length > 0);
      return;
    }

    const sub = parts[1].toLowerCase();

    // Level 2+: nested subcommand trees (e.g. /workspace folder → remove/clear)
    if (parts.length >= 3 && tree.nested) {
      const nested = tree.nested[sub];
      if (nested) {
        if (parts.length === 3) {
          const part2 = parts[2] || '';
          const q = part2.toLowerCase();
          const result: CompletionItem[] = [];
          if (nested.subs) {
            for (const s of nested.subs) {
              if (s.toLowerCase().startsWith(q)) {
                result.push({ label: s, desc: '', value: cmd + ' ' + parts[1] + ' ' + s, level: 2 });
              }
            }
          }
          const nDynKey = nested.dynamic && (
            nested.dynamic[part2] !== undefined ? part2 :
            nested.dynamic[''] !== undefined ? '' : null
          );
          if (nDynKey !== null && nested.dynamic) {
            const kind = nested.dynamic[nDynKey];
            await addDynItems(result, kind, q, cmd + ' ' + parts[1] + ' ', 2);
          }
          setSelectedIdx(0);
          setItems(result);
          setVisible(result.length > 0);
          return;
        }
        if (parts.length === 4 && nested.dynamic) {
          const subSub = parts[2].toLowerCase();
          const kind = nested.dynamic[subSub];
          if (kind) {
            const result: CompletionItem[] = [];
            await addDynItems(result, kind, parts[3].toLowerCase(), cmd + ' ' + parts[1] + ' ' + parts[2] + ' ', 3);
            setSelectedIdx(0);
            setItems(result);
            setVisible(result.length > 0);
            return;
          }
        }
      }
    }

    // Dynamic arg after a subcommand (e.g. /session load <name>,
    // /tool enable <name>, /feature load <id>)
    if (parts.length === 3 && tree.dynamic) {
      const kind = tree.dynamic[sub];
      if (kind) {
        const result: CompletionItem[] = [];
        await addDynItems(result, kind, parts[2].toLowerCase(), cmd + ' ' + parts[1] + ' ', 2);
        setSelectedIdx(0);
        setItems(result);
        setVisible(result.length > 0);
        return;
      }
    }

    // No matches at this depth
    close();
  }, [commands, close, addDynItems]);

  // Accept the currently selected suggestion.
  // Returns the new text to set in the input, or null if nothing selected.
  const accept = useCallback((): string | null => {
    if (!items.length) return null;
    const item = items[selectedIdx];
    const hasMore = item.level === 0 && SUB_TREE[item.value];
    const newText = item.value + ' ';
    if (hasMore) {
      // Keep the dropdown open for subcommand completion.
      // Caller should call update(newText) after setting input.
    } else {
      setVisible(false);
      setItems([]);
      setSelectedIdx(0);
    }
    return newText;
  }, [items, selectedIdx]);

  return {
    visible,
    items,
    selectedIdx,
    update,
    close,
    moveUp,
    moveDown,
    accept,
    invalidateCache,
  };
}
