// Work (documentation/plans/mobile_parity.md §6 Work): the Work tab (Now · Jobs · Tickets ·
// History as header tabs), and the pushed pages — a job, Activity, Settings, Setup (with Doctor)
// and a job's performance. Pages with a PageHeader hide the native header.

import React from 'react';
import { WorkScreen } from '../../screens/WorkScreen';
import { JobDetailScreen } from '../../screens/JobDetailScreen';
import { JobAnalysisScreen } from '../../screens/JobAnalysisScreen';
import { JobActivityScreen } from '../../screens/JobActivityScreen';
import { JobSettingsScreen } from '../../screens/JobSettingsScreen';
import { JobSetupScreen } from '../../screens/JobSetupScreen';
import { Stage } from '../../ui/Glass';
import type { ScreenSpec, TabSpec } from '../types';

export type WorkParams = {
  /** A job; `tab` opens one of its tabs (notes, session, plan, diff, review, checks, diagnostics, costs);
   * `action` starts one of its actions (the Now board's decisive action). */
  JobDetail: { jobId: string; tab?: string; action?: string };
  JobAnalysis: { jobId: string };
  JobActivity: undefined;
  JobSettings: undefined;
  /** Setup with its two tabs: the checklist (default) and Doctor. */
  JobSetup: { tab?: 'checklist' | 'doctor' } | undefined;
};

/** The Work tab's own params (`tab`: now, jobs, tickets, history). */
export type WorkTabParams = { tab?: string } | undefined;

const WorkTab = (props: any) => <Stage><WorkScreen {...props} /></Stage>;

export const workTab: TabSpec = { name: 'Work', component: WorkTab, label: 'Work', icon: 'briefcase', destination: 'work', headerShown: false };

const own = { headerShown: false };

export const workScreens: ScreenSpec[] = [
  { name: 'JobDetail', component: JobDetailScreen, options: own },
  { name: 'JobAnalysis', component: JobAnalysisScreen, options: { title: 'Job performance', headerShown: false } },
  { name: 'JobSetup', component: JobSetupScreen, options: own },
  { name: 'JobActivity', component: JobActivityScreen, options: { ...own, animation: 'fade' } },
  { name: 'JobSettings', component: JobSettingsScreen, options: { ...own, animation: 'fade' } },
];
