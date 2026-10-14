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

it('calls the CONTRACT endpoints for push preview, viewed marks, activity and status', async () => {
  await jobCoreApi.pushPreview('j/1');
  expect(api.get).toHaveBeenLastCalledWith('/api/jobs/vm/job/j%2F1/push-preview');
  await jobCoreApi.markViewed('j', 'src/a.py', true);
  expect(api.post).toHaveBeenLastCalledWith('/api/jobs/cmd', {type:'markViewed',job_id:'j',args:{path:'src/a.py',viewed:true}});
  await jobCoreApi.activity();
  expect(api.get).toHaveBeenLastCalledWith('/api/jobs/vm/activity', {timeoutMs:8000});
  await jobCoreApi.activityLogs('j', 1024);
  expect(api.get).toHaveBeenLastCalledWith('/api/jobs/vm/activity/logs/j', {query:{tail_bytes:1024}});
  await jobCoreApi.removeContainer('mucli-job-0123456789ab-impl');
  expect(api.post).toHaveBeenLastCalledWith('/api/jobs/activity/containers/mucli-job-0123456789ab-impl/remove', {});
  await jobCoreApi.status();
  expect(api.get).toHaveBeenLastCalledWith('/api/jobs/vm/status', {timeoutMs:5000});
});
