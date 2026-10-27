import './styles.css';
import '@fontsource-variable/inter';

import { applyTheme, type OmniTheme, themeDefNames } from '@omniagents/ui/themes';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { WebApp } from './WebApp';

/** `?theme=<name>` picks one of the shared themes. */
function pageTheme(): OmniTheme {
  const requested = new URL(window.location.href).searchParams.get('theme');
  return themeDefNames.find((name) => name === requested) ?? 'omni';
}

const theme = pageTheme();
applyTheme(theme);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <WebApp theme={theme} />
  </StrictMode>
);
