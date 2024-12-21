import Store from 'electron-store';

import { schema } from '@/shared/types';

export const store = new Store({ schema, clearInvalidConfig: true, watch: true });
