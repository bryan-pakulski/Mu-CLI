// --- state store + reducers -------------------------------------------------
const state = { models: {}, messages: [], traces: [], pricing: {}, sessionTurns: [], uploads: [], pendingApproval: null, tools: [], customToolErrors: [], backgroundJobs: [], sessions: [], activeSession: '', gitRepos: [], gitBranches: [], gitCurrentRepo: null, gitCurrentBranch: null, gitDiff: '', skills: [], enabledSkills: [], workspaceIndexStats: {}, uiSurface: 'operate', pinnedSessions: [], recentSessions: [], gitDiffMode: 'inline', gitHunkDecisions: {}, timelineFilter: 'all', gitDiffStats: { files: 0, additions: 0, deletions: 0 } };
let syncing = false;
let applyTimer = null;
let sending = false;
let approvalPoll = null;
let selectedDir = null;
let lastApprovalPatchFingerprint = null;
let openSessionMenuFor = null;
let planApprovalResolver = null;
const completedSeenSessions = new Set();
let runtimeTick = null;
let backgroundJobPoll = null;
let sendingSession = null;
const runNoticesBySession = {};
const runDetailsById = {};
