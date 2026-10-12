import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { createHttpApi } from './api/client';
import { createMockApi } from './api/mockApi';
import { config } from './config';
import { AppStateProvider } from './state/AppStateProvider';
import { initTheme } from './theme/theme';
import './styles/tokens.css';
import './styles/base.css';

initTheme();

const api =
  config.apiMode === 'mock'
    ? createMockApi()
    : createHttpApi({ baseUrl: config.apiBaseUrl, timeoutMs: config.requestTimeoutMs });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppStateProvider api={api}>
      <App />
    </AppStateProvider>
  </StrictMode>,
);
