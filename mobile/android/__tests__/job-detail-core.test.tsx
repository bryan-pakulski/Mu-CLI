import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { JobDetailScreen } from '../src/screens/JobDetailScreen';
import { jobCoreApi, subscribeToJobChanges, type JobVM } from '../src/api/jobCore';

jest.mock('../src/api/jobCore', () => ({jobCoreApi:{job:jest.fn(),command:jest.fn()},subscribeToJobChanges:jest.fn(()=>({close:jest.fn()}))}));
jest.mock('../src/components/JobStageModelsSheet', () => ({JobStageModelsSheet:()=>null}));

const vm = {
  id:'j',title:'Ticket',key:'T-1',state:'plan_review',cost_usd:0,branch:'job/j',pipeline:[],
  actions:[{id:'savePlanDraft',label:'Save draft',enabled:true},{id:'approvePlan',label:'Approve plan',enabled:true}],
  plan:{version:1,latest_version:1,approved_version:0,versions:[1],markdown:'Original',draft:null},
} as unknown as JobVM;

it('keeps mobile edits during partial refresh and approves the saved version', async () => {
  (jobCoreApi.job as jest.Mock).mockResolvedValue(vm);
  (jobCoreApi.command as jest.Mock).mockResolvedValue({ok:true,parts:['plan'],data:{version:2}});
  const navigation = {setOptions:jest.fn(),addListener:jest.fn(()=>jest.fn()),navigate:jest.fn()};
  const screen = render(<JobDetailScreen route={{params:{jobId:'j'}} as any} navigation={navigation as any} />);
  const editor = await screen.findByLabelText('Plan Markdown');
  fireEvent.changeText(editor, 'Human edits');
  (jobCoreApi.job as jest.Mock).mockResolvedValue({...vm,plan:undefined});
  const onChange = (subscribeToJobChanges as jest.Mock).mock.calls[0][0];
  await act(async () => { onChange({scope:'job',id:'j',parts:['costs']}); });
  expect(screen.getByLabelText('Plan Markdown').props.value).toBe('Human edits');
  (jobCoreApi.job as jest.Mock).mockResolvedValue({...vm,plan:{...vm.plan,version:2,latest_version:2,markdown:'Human edits'}});
  fireEvent.press(screen.getByText('Save draft'));
  await waitFor(()=>expect(jobCoreApi.command).toHaveBeenCalledWith('j','savePlanDraft',{plan:'Human edits',version:1}));
  await waitFor(()=>expect(screen.getByText(/Version 2/)).toBeTruthy());
  fireEvent.press(screen.getByText('Approve plan'));
  await waitFor(()=>expect(jobCoreApi.command).toHaveBeenLastCalledWith('j','approvePlan',{version:2}));
  screen.unmount();
});
