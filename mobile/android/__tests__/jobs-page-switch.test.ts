import { switchJobsPage } from '../src/components/JobsChrome';

describe('switchJobsPage', () => {
  it('replaces the current jobs tab instead of pushing history', () => {
    const navigation = { replace: jest.fn(), navigate: jest.fn() };
    switchJobsPage(navigation, 'Work', 'JobActivity');
    expect(navigation.replace).toHaveBeenCalledWith('JobActivity');
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it('ignores a switch to the current tab', () => {
    const navigation = { replace: jest.fn(), navigate: jest.fn() };
    switchJobsPage(navigation, 'JobsList', 'JobsList');
    expect(navigation.replace).not.toHaveBeenCalled();
  });
});
