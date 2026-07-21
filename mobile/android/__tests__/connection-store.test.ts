import { useConnectionStore } from '../src/store/connection';

// AsyncStorage mocked globally in jest.setup.ts

describe('Connection store', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useConnectionStore.setState({
      baseUrl: 'http://localhost:30311',
      activeSessionName: null,
      activeProvider: null,
      activeModel: null,
      isConnected: false,
    });
  });

  it('has correct initial state', () => {
    const state = useConnectionStore.getState();
    expect(state.baseUrl).toBe('http://localhost:30311');
    expect(state.activeSessionName).toBeNull();
    expect(state.isConnected).toBe(false);
  });

  it('setBaseUrl strips trailing slash', () => {
    useConnectionStore.getState().setBaseUrl('http://test:8000/');
    expect(useConnectionStore.getState().baseUrl).toBe('http://test:8000');
  });

  it('setActiveSession updates state', () => {
    useConnectionStore.getState().setActiveSession('test-session');
    expect(useConnectionStore.getState().activeSessionName).toBe('test-session');
  });

  it('setActiveProviderModel updates both provider and model', () => {
    useConnectionStore.getState().setActiveProviderModel('openai', 'gpt-4');
    const state = useConnectionStore.getState();
    expect(state.activeProvider).toBe('openai');
    expect(state.activeModel).toBe('gpt-4');
  });

  it('setConnected toggles connected state', () => {
    useConnectionStore.getState().setConnected(true);
    expect(useConnectionStore.getState().isConnected).toBe(true);
    useConnectionStore.getState().setConnected(false);
    expect(useConnectionStore.getState().isConnected).toBe(false);
  });
});