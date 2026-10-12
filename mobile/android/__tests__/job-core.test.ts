import { api } from '../src/api/client';
import { jobCoreApi } from '../src/api/jobCore';

jest.mock('../src/api/client', () => ({ api:{get:jest.fn(),post:jest.fn()} }));
jest.mock('../src/api/sse', () => ({ subscribeToEvents:jest.fn() }));

it('requests only the changed parts and the selected plan version', async () => {
  await jobCoreApi.job('job / 1', ['plan','state'], 2);
  expect(api.get).toHaveBeenCalledWith('/api/jobs/vm/job/job%20%2F%201', {query:{parts:'plan,state',plan_version:2}});
});

it('sends the reviewed version and selected findings through the shared command endpoint', async () => {
  await jobCoreApi.command('j', 'approvePlan', {version:3});
  expect(api.post).toHaveBeenLastCalledWith('/api/jobs/cmd', {type:'approvePlan',job_id:'j',args:{version:3}});
  await jobCoreApi.command('j', 'reprompt', {finding_ids:['F-2'],guidance:'Address the race'});
  expect(api.post).toHaveBeenLastCalledWith('/api/jobs/cmd', {type:'reprompt',job_id:'j',args:{finding_ids:['F-2'],guidance:'Address the race'}});
});
