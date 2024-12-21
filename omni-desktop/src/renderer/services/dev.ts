import { ipc } from '@/renderer/services/ipc';

ipc.on('dev:console-log', (_, data) => {
  console.log(data);
});
