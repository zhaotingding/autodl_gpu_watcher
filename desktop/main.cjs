const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeTheme, net, protocol, session, shell } = require('electron')
const { spawn } = require('node:child_process')
const { createInterface } = require('node:readline')
const { pathToFileURL } = require('node:url')
const path = require('node:path')

const projectRoot = path.dirname(__dirname)
const dataRoot = path.resolve(process.env.AUTODL_APP_ROOT || projectRoot)
const entry = 'autogpu://app/index.html'
const pythonIndex = process.argv.indexOf('--python')
const python = pythonIndex >= 0 ? process.argv[pythonIndex + 1] : process.env.AUTODL_PYTHON
const methods = new Set([
  'snapshot', 'preferences.save', 'servers.save', 'servers.pin', 'monitor.start', 'monitor.stop', 'monitor.refresh',
  'login.start', 'login.finish', 'training.start', 'training.stop', 'training.log', 'network.snapshot',
  'ai.settings', 'ai.save', 'ai.delete_key', 'ai.generate', 'ai.usage', 'telemetry.delete_password',
  'icon.settings', 'icon.import', 'icon.reset', 'icon.shortcut', 'shutdown',
])
const resources = new Set(['index.html', 'renderer.js', 'styles.css'])

app.setName('Auto算力与训练调度')
app.setPath('userData', path.join(dataRoot, '.ui', 'electron'))
protocol.registerSchemesAsPrivileged([{ scheme: 'autogpu', privileges: {
  standard: true, secure: true, supportFetchAPI: true,
} }])

let window
let child
let serial = 0
let closing = false
let shutdown = false
let selectedIcon = null
let selectedShortcut = null
let backendFailure = ''
let applicationVersion = ''
const pending = new Map()

function titleBar(theme) {
  return theme === 'light'
    ? { color: '#ececec', symbolColor: '#242424', height: 44 }
    : { color: '#242424', symbolColor: '#dedede', height: 44 }
}

function popupMenu(name, position) {
  const navigate = page => emit('navigate', { page })
  const menus = {
    file: [
      { label: '全局设置', accelerator: 'CmdOrCtrl+,', click: () => navigate('settings') },
      { label: '打开资料目录', click: () => { void shell.openPath(dataRoot) } },
      { type: 'separator' },
      { label: '退出', accelerator: 'Alt+F4', click: () => window.close() },
    ],
    edit: [
      { label: '撤销', role: 'undo' }, { label: '重做', role: 'redo' }, { type: 'separator' },
      { label: '剪切', role: 'cut' }, { label: '复制', role: 'copy' }, { label: '粘贴', role: 'paste' },
      { type: 'separator' }, { label: '全选', role: 'selectAll' },
    ],
    view: [
      { label: 'AutoDL 平台', click: () => navigate('platform') },
      { label: '网络连接详情', click: () => navigate('network') },
      { label: '切换深浅主题', click: () => emit('toggle-theme', {}) },
      { type: 'separator' },
      { label: '放大', role: 'zoomIn' }, { label: '缩小', role: 'zoomOut' },
      { label: '实际大小', role: 'resetZoom' }, { label: '全屏', role: 'togglefullscreen' },
    ],
    help: [
      { label: '使用说明', click: () => { void shell.openPath(path.join(projectRoot, 'README.md')) } },
      { label: '关于 Auto算力与训练调度', click: () => {
        void dialog.showMessageBox(window, { type: 'info', title: '关于', message: 'Auto算力与训练调度',
          detail: '版本 ' + applicationVersion + '\nElectron界面 · Python监控后台' })
      } },
    ],
  }
  if (!Object.hasOwn(menus, name)) throw new Error('未知菜单。')
  const [width, height] = window.getContentSize()
  const x = Number.isFinite(position.x) ? Math.max(0, Math.min(width, Math.round(position.x))) : 12
  const y = Number.isFinite(position.y) ? Math.max(0, Math.min(height, Math.round(position.y))) : 44
  return new Promise(resolve => {
    const menu = Menu.buildFromTemplate(menus[name])
    menu.popup({ window, x, y, callback: () => resolve(true) })
  })
}

function emit(event, data) {
  if (window && !window.isDestroyed()) window.webContents.send('watcher:event', { event, data })
}

function failPending(error) {
  for (const request of pending.values()) request.reject(error)
  pending.clear()
}

function backend(method, params = {}) {
  if (!child || child.exitCode !== null || !child.stdin.writable) {
    return Promise.reject(new Error('Python桌面后台未运行，请检查所选Conda环境和启动输出。'))
  }
  return new Promise((resolve, reject) => {
    const id = ++serial
    pending.set(id, { resolve, reject })
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n', error => {
      if (!error) return
      pending.delete(id)
      reject(error)
    })
  })
}

function startBackend() {
  if (!python || !path.isAbsolute(python)) {
    throw new Error('请从项目Conda环境启动应用，或通过 --python 指定该项目解释器的完整路径。')
  }
  const environment = { ...process.env, AUTODL_APP_ROOT: dataRoot, AUTODL_DESKTOP_PID: String(process.pid),
    PYTHONUTF8: '1', PYTHONNOUSERSITE: '1', PYTHONPATH: path.join(projectRoot, 'src') }
  if (process.argv.includes('--debug')) environment.AUTODL_DEBUG = '1'
  delete environment.PYTHONHOME
  child = spawn(python, ['-s', '-u', '-m', 'autodl_watcher.desktop_backend'], {
    cwd: projectRoot, env: environment, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  })
  // 请求正文、口令及完整后台诊断不写入Electron控制台或页面日志。
  child.stderr.on('data', () => {})
  child.stdin.on('error', () => {})
  child.on('error', error => {
    failPending(error)
    emit('backend.error', { message: '无法启动所选Python解释器，请检查项目环境。' })
  })
  child.on('close', code => {
    const message = backendFailure || 'Python桌面后台已退出（退出码 ' + code + '）。'
    failPending(new Error(message))
    emit('backend.exit', { code, message })
  })
  createInterface({ input: child.stdout }).on('line', line => {
    let response
    try {
      response = JSON.parse(line)
    } catch {
      // stdout是约定的JSONL通道，普通后台输出应由Python变成日志事件。
      failPending(new Error('Python后台返回了无效的桌面协议消息。'))
      emit('backend.error', { message: 'Python后台消息格式无效，请检查桌面桥接服务。' })
      return
    }
    if (response.event) {
      if (response.event === 'backend.error' && typeof response.data?.message === 'string') {
        backendFailure = response.data.message
        failPending(new Error(backendFailure))  // Python仅传递已脱敏的启动错误，保留可操作原因。
      }
      emit(response.event, response.data)
      return
    }
    const request = pending.get(response.id)
    if (!request) return
    pending.delete(response.id)
    if (response.error) request.reject(new Error(String(response.error)))
    else request.resolve(response.result)
  })
}

function checkSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) {
    throw new Error('只接受应用主窗口操作。')
  }
  const url = new URL(event.senderFrame.url)
  if (url.protocol !== 'autogpu:' || url.hostname !== 'app' || url.pathname !== '/index.html') {
    throw new Error('界面来源无效。')
  }
}

function objectParams(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('操作参数格式无效。')
}

async function requestClose() {
  if (closing || shutdown) return
  closing = true
  emit('closing', { message: '正在停止采集并等待后台保存结果…' })
  try {
    await backend('shutdown')
    shutdown = true
    child.stdin.end()
    window.destroy()
    app.quit()
  } catch (error) {
    if (child.exitCode !== null || !child.stdin.writable) {
      shutdown = true
      window.destroy()
      app.quit()
      return
    }
    closing = false
    emit('close.cancelled', { message: error.message })
    await dialog.showMessageBox(window, { type: 'warning', title: '关闭尚未完成', message: error.message })
  }
}

async function invoke(event, method, params) {
  checkSender(event)
  objectParams(params)
  if (method === 'desktop.confirm') {
    const result = await dialog.showMessageBox(window, {
      type: 'question', title: String(params.title || '确认操作'), message: String(params.message || ''),
      buttons: ['取消', '确认'], defaultId: 0, cancelId: 0, noLink: true,
    })
    return result.response === 1
  }
  if (method === 'desktop.copy') {
    clipboard.writeText(String(params.text || ''))
    return true
  }
  if (method === 'desktop.theme') {
    if (!['dark', 'light'].includes(params.theme)) throw new Error('主题参数无效。')
    nativeTheme.themeSource = params.theme
    window.setBackgroundColor(params.theme === 'light' ? '#fafafa' : '#1b1b1b')
    window.setTitleBarOverlay(titleBar(params.theme))
    return true
  }
  if (method === 'desktop.menu') return popupMenu(params.menu, params)
  if (method === 'desktop.openExternal') {
    const target = new URL(params.url)
    if (target.protocol !== 'https:') throw new Error('外部链接须使用HTTPS。')
    await shell.openExternal(target.href)
    return true
  }
  if (method === 'desktop.close') {
    window.close()
    return true
  }
  if (!methods.has(method)) throw new Error('未知的桌面操作。')
  if (method === 'icon.import') {
    if (!params.path || params.path !== selectedIcon) throw new Error('请通过应用文件对话框选择图标。')
    selectedIcon = null
  }
  if (method === 'icon.shortcut') {
    if (!params.path || params.path !== selectedShortcut) throw new Error('请通过应用另存对话框选择快捷方式位置。')
    selectedShortcut = null
  }
  const result = await backend(method, params)
  if ((method === 'icon.import' || method === 'icon.reset') && result?.path) window.setIcon(result.path)
  return result
}

async function chooseFile(event, options) {
  checkSender(event)
  objectParams(options)
  if (options.kind === 'shortcut') {
    const result = await dialog.showSaveDialog(window, { title: '另存启动快捷方式', defaultPath: 'Auto算力与训练调度.lnk',
      filters: [{ name: 'Windows快捷方式', extensions: ['lnk'] }] })
    selectedShortcut = result.canceled ? null : result.filePath
    return selectedShortcut
  }
  if (options.kind !== 'icon') throw new Error('请选择支持的文件类型。')
  const result = await dialog.showOpenDialog(window, { title: '选择应用图标', properties: ['openFile'],
    filters: [{ name: '应用图标', extensions: ['ico', 'png'] }] })
  selectedIcon = result.canceled ? null : result.filePaths[0]
  return selectedIcon
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!window) return
    if (window.isMinimized()) window.restore()
    window.focus()
  })
  app.whenReady().then(async () => {
    session.defaultSession.setSpellCheckerEnabled(false)
    // Chromium仍会初始化词典下载器，将它限定到本地404路径，避免访问默认CDN。
    session.defaultSession.setSpellCheckerDictionaryDownloadURL('autogpu://app/dictionaries/')
    protocol.handle('autogpu', request => {
      const url = new URL(request.url)
      const file = url.pathname.slice(1)
      if (url.hostname !== 'app' || !resources.has(file)) return new Response('', { status: 404 })
      return net.fetch(pathToFileURL(path.join(__dirname, file)).href)
    })
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
    Menu.setApplicationMenu(null)
    app.setAppUserModelId('AutoDL.GPUWatcher')
    startBackend()
    const state = await backend('snapshot')
    applicationVersion = state.app.version
    nativeTheme.themeSource = state.theme === 'light' ? 'light' : 'dark'
    window = new BrowserWindow({ title: 'Auto算力与训练调度', width: 1440, height: 900,
      minWidth: 860, minHeight: 660, icon: state.icon?.path || path.join(projectRoot, 'src', 'autodl_watcher', 'default.ico'),
      backgroundColor: state.theme === 'light' ? '#fafafa' : '#1b1b1b',
      titleBarStyle: 'hidden', titleBarOverlay: titleBar(state.theme),
      show: false,
      // 禁用Chromium拼写检查，避免为模型ID、日志和代码输入自动下载英语词典。
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false,
        spellcheck: false },
    })
    ipcMain.handle('watcher:invoke', invoke)
    ipcMain.handle('watcher:chooseFile', chooseFile)
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    window.webContents.on('will-navigate', event => event.preventDefault())
    window.on('close', event => {
      if (shutdown || child.exitCode !== null || !child.stdin.writable) return
      event.preventDefault()
      void requestClose()
    })
    window.once('ready-to-show', () => window.show())
    await window.loadURL(entry)
  }).catch(error => {
    child?.stdin.end()
    dialog.showErrorBox('Auto算力与训练调度启动失败', error.message)
    app.exit(1)
  })
  app.on('window-all-closed', () => app.quit())
}
