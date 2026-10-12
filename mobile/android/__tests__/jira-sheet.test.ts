import { jiraTicketQuery, type JiraTicket } from '../src/api/jobs';
import { filterTickets } from '../src/components/JiraSheet';

const ticket = (key: string, status: string): JiraTicket => ({
  key, summary: key, url: '', status, status_category: '', issue_type: '', priority: '', assignee: '', labels: [],
});

describe('Jira sheet helpers', () => {
  const all = [ticket('A-1', 'To Do'), ticket('A-2', 'Blocked'), ticket('A-3', 'In Progress')];

  it('shows every ticket when no status is selected', () => {
    expect(filterTickets(all, [])).toHaveLength(3);
  });

  it('keeps only selected statuses', () => {
    expect(filterTickets(all, ['To Do', 'Blocked']).map(t => t.key)).toEqual(['A-1', 'A-2']);
  });

  it('drops empty/false query values so done tickets stay hidden by default', () => {
    expect(jiraTicketQuery({ board_id: 7, include_done: false, jql: '' })).toEqual({ board_id: 7 });
  });
});
