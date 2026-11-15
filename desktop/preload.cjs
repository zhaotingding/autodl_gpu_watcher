const { contextBridge, ipcRenderer } = require('electron')

// 渲染层只得到业务调用和事件订阅，不暴露Node、文件系统或完整IPC对象。
contextBridge.exposeInMainWorld('watcher', {
  invoke: (method, params = {}) => ipcRenderer.invoke('watcher:invoke', method, params),
  chooseFile: (options = {}) => ipcRenderer.invoke('watcher:chooseFile', options),
  onEvent(callback) {
    if (typeof callback !== 'function') throw new TypeError('事件监听器须为函数')
    const listener = (_event, value) => callback(value)
    ipcRenderer.on('watcher:event', listener)
    return () => ipcRenderer.removeListener('watcher:event', listener)
  },
})
