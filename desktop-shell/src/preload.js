import { contextBridge, ipcRenderer } from 'electron';

const api = {
  health: () => ipcRenderer.invoke('galaxy:health'),
  getToken: () => ipcRenderer.invoke('galaxy:getToken'),
  onToken: (callback) => {
    const handler = (_event, token) => callback(token);
    ipcRenderer.on('galaxy:token', handler);
    return () => ipcRenderer.removeListener('galaxy:token', handler);
  },
  openExternal: (url) => ipcRenderer.invoke('galaxy:openExternal', url),
};

contextBridge.exposeInMainWorld('galaxy', api);