// The session's agent modes and the current one (composer mode chip, session settings, the
// workspace panel's "This mode").

import { useCallback, useEffect, useState } from 'react';
import { modesApi, type ModeInfo } from '../../api/modes';

export function useModes(sessionName: string | null) {
  const [modes, setModes] = useState<ModeInfo[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!sessionName) { setModes([]); setCurrent(null); return; }
    try {
      const response = await modesApi.list();
      setModes(response.modes || []);
      setCurrent(response.current || 'default');
    } catch {
      // Transport errors are the connection layer's to show.
    }
  }, [sessionName]);

  useEffect(() => { void reload(); }, [reload]);

  const select = useCallback(async (name: string) => {
    setError(null);
    try {
      await modesApi.set(name);
      setCurrent(name);
      return true;
    } catch (cause) {
      setError(`Couldn't switch mode: ${cause instanceof Error ? cause.message : String(cause)}`);
      return false;
    }
  }, []);

  const label = modes.find(m => m.name === current)?.display_name || (current ? current.charAt(0).toUpperCase() + current.slice(1) : 'Default');
  return { modes, current, label, error, reload, select };
}
