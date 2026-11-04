// The focused tab page's title, accessory and options, shown in the tab shell's top bar. A tab's
// PageHeader registers here (through TopBarContext); only the top bar subscribes, so a page
// re-registering on every render never re-renders the pages themselves.

import { create } from 'zustand';
import type { BarPage } from '../ui/Mu';

type BarPageState = {
  owner: string | null;
  page: BarPage | null;
  register: (owner: string, page: BarPage | null) => void;
};

export const useBarPage = create<BarPageState>(set => ({
  owner: null,
  page: null,
  register: (owner, page) => set(state => {
    if (page) return { owner, page };
    return state.owner === owner ? { owner: null, page: null } : state;
  }),
}));
