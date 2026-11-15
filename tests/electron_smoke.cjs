// 在隔离资料根启动实际Electron和Python桥接，所有业务操作均为本地配置及只读快照。
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { spawn, spawnSync } = require('node:child_process')

const root = path.resolve(__dirname, '..')
const desktop = path.join(root, 'desktop')
const runtime = path.join(desktop, 'node_modules', 'electron', 'dist', process.platform === 'win32' ? 'electron.exe' : 'electron')

// 旧系统Node缺少WebSocket时，仅测试驱动使用本项目Electron内置Node。
if (typeof WebSocket === 'undefined') {
  const result = spawnSync(runtime, [__filename, ...process.argv.slice(2)], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, stdio: 'inherit', windowsHide: true,
  })
  if (result.error) throw result.error
  process.exit(result.status ?? 1)
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function wait(check, message, attempts = 160) {
  for (let i = 0; i < attempts; i++) {
    const result = await check()
    if (result) return result
    await pause(150)
  }
  throw new Error(message)
}

async function run() {
  const python = process.env.AUTODL_PYTHON || (process.env.CONDA_PREFIX && path.join(process.env.CONDA_PREFIX,
    process.platform === 'win32' ? 'python.exe' : 'bin/python'))
  assert.ok(python && path.isAbsolute(python), '请通过项目Conda环境运行本测试，或明确设置AUTODL_PYTHON。')
  const isolated = await fs.mkdtemp(path.join(os.tmpdir(), 'autogpu-electron-'))
  const launcherMode = process.argv.includes('--launcher')
  const sourceMode = process.argv.includes('--source-entry')
  assert.ok(!(launcherMode && sourceMode), '请选择一种启动入口验收。')
  const junctions = []
  let executable = runtime
  const networkLog = path.join(isolated, 'chromium-network.json')
  const debugArguments = ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--log-net-log=' + networkLog]
  let argumentsList = [desktop, ...debugArguments]
  if (sourceMode) {
    executable = python
    argumentsList = ['-s', '-m', 'autodl_watcher', ...debugArguments]
  }
  if (launcherMode) {
    assert.equal(process.platform, 'win32', '轻量EXE入口验收仅适用Windows。')
    executable = path.join(isolated, 'AutoDLWatcher.exe')
    await fs.copyFile(path.join(root, 'AutoDLWatcher.exe'), executable)
    for (const name of ['src', 'tools']) {
      const junction = path.join(isolated, name)
      await fs.symlink(path.join(root, name), junction, 'junction')
      junctions.push({ path: junction, target: path.join(root, name) })
    }
    argumentsList = ['--env', process.env.CONDA_DEFAULT_ENV || 'auto-gpu', ...debugArguments]
  }
  await fs.mkdir(path.join(isolated, '.ui'))
  await fs.writeFile(path.join(isolated, '.ui', 'preferences.yaml'),
    'ui:\n  theme: dark\nuser: "测试用户"\npoll: "120"\nlive: false\ncustom_preserved: kept\n')
  const env = { ...process.env, AUTODL_APP_ROOT: isolated, AUTODL_PYTHON: python,
    PYTHONUTF8: '1', PYTHONNOUSERSITE: '1', PYTHONPATH: path.join(root, 'src') }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.PYTHONHOME
  const processHandle = spawn(executable, argumentsList, {
    cwd: root, env, windowsHide: false, stdio: ['ignore', 'ignore', 'pipe'],
  })
  let diagnostic = ''
  processHandle.stderr.setEncoding('utf8').on('data', value => { diagnostic = (diagnostic + value).slice(-3000) })
  const exited = new Promise(resolve => processHandle.once('exit', resolve))
  let socket
  let evaluate
  let send
  let windowClosed
  let succeeded = false
  try {
    const port = await wait(async () => {
      if (processHandle.exitCode !== null && (!launcherMode || processHandle.exitCode !== 0)) {
        throw new Error('桌面入口启动失败：' + diagnostic)
      }
      const value = await fs.readFile(path.join(isolated, '.ui', 'electron', 'DevToolsActivePort'), 'utf8').catch(() => '')
      return value.split('\n')[0].trim()
    }, '未取得本机Electron调试端口。')
    const page = await wait(async () => {
      const pages = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json()
      return pages.find(item => item.url === 'autogpu://app/index.html')
    }, 'Electron界面未加载：' + diagnostic)
    socket = new WebSocket(page.webSocketDebuggerUrl)
    windowClosed = new Promise(resolve => socket.addEventListener('close', resolve, { once: true }))
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true })
      socket.addEventListener('error', reject, { once: true })
    })
    let serial = 0
    const pending = new Map()
    socket.addEventListener('message', event => {
      const response = JSON.parse(event.data)
      const request = pending.get(response.id)
      if (request) {
        pending.delete(response.id)
        clearTimeout(request.timer)
        request.resolve(response)
      }
    })
    socket.addEventListener('close', () => {
      for (const request of pending.values()) {
        clearTimeout(request.timer)
        request.reject(new Error('Electron调试连接已关闭。'))
      }
      pending.clear()
    })
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++serial
      const timer = setTimeout(() => {
        pending.delete(id)
        reject(new Error('Electron验收调用超时 ' + method))
      }, 15000)
      pending.set(id, { resolve, reject, timer })
      socket.send(JSON.stringify({ id, method, params }))
    })
    evaluate = async expression => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      assert.ok(!response.error, JSON.stringify(response.error))
      if (response.result.exceptionDetails) {
        throw new Error(response.result.result.description || response.result.exceptionDetails.text)
      }
      return response.result.result.value
    }
    await wait(() => evaluate("typeof window.watcher?.invoke === 'function' && document.body.innerText.includes('Auto')"), '桌面界面与preload未就绪。')
    assert.equal(await evaluate('typeof require'), 'undefined')
    assert.equal(await evaluate('typeof process'), 'undefined')
    let state = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(state.theme, 'dark')
    assert.equal(state.worker.running, false)
    assert.deepEqual(state.servers, [])
    assert.ok(state.app.version)
    assert.equal(path.normalize(state.app.python), path.normalize(python))
    assert.equal(path.normalize(state.app.root), path.normalize(isolated))

    // 从实际设置控件走保存链路，再读取Python状态及磁盘YAML。
    await evaluate("document.querySelector('#settings-nav').click()")
    await wait(() => evaluate("!!document.querySelector('#monitor-form')"), '监控设置未打开。')
    await evaluate("(() => { const theme=document.querySelector('#theme-choice'); theme.value='light'; theme.dispatchEvent(new Event('change',{bubbles:true})); })()")
    await wait(() => evaluate("window.watcher.invoke('snapshot').then(s=>s.theme==='light' && document.documentElement.dataset.theme==='light')"), '主题控件未保存并应用浅色。')
    await evaluate("(() => { document.querySelector('#monitor-user').value='界面验收用户'; document.querySelector('#monitor-form').requestSubmit(); })()")
    await wait(() => evaluate("window.watcher.invoke('snapshot').then(s=>s.preferences.user==='界面验收用户')"), '监控设置表单未保存。')
    state = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(state.theme, 'light')
    assert.match(await fs.readFile(path.join(isolated, '.ui', 'preferences.yaml'), 'utf8'), /custom_preserved: kept/)
    assert.match(await fs.readFile(path.join(isolated, '.ui', 'preferences.yaml'), 'utf8'), /theme: light/)
    await evaluate("document.querySelector('[data-settings=servers]').click()")
    await wait(() => evaluate("!!document.querySelector('#server-form')"), '服务器设置未打开。')
    await evaluate("(() => { document.querySelector('#server-name').value='隔离服务器'; document.querySelector('#server-alias').value='example-fixture'; document.querySelector('#server-pinned').checked=true; document.querySelector('#server-form').requestSubmit(); })()")
    await wait(() => evaluate("window.watcher.invoke('snapshot').then(s=>s.servers.length===1 && s.servers[0].pinned)"), '服务器表单未保存。')
    const server = (await evaluate("window.watcher.invoke('snapshot')")).servers[0]
    assert.ok(server.id)
    state = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(state.servers.length, 1)
    assert.equal(state.servers[0].pinned, true)
    assert.equal(state.servers[0].status.active, false)
    await wait(() => evaluate(`!!document.querySelector('[data-page="${server.id}"]')`), '固定的服务器未出现在导航。')
    await evaluate(`document.querySelector('[data-page="${server.id}"]').click()`)
    await wait(() => evaluate("!!document.querySelector('[data-action=training-start]')"), '独立训练页面未打开。')
    await evaluate("document.querySelector('[data-action=server-settings]').click()")
    await wait(() => evaluate("!!document.querySelector('#server-pinned')"), '连接设置入口未打开。')
    await evaluate("document.querySelector('#server-pinned').click()")
    await wait(() => evaluate("window.watcher.invoke('snapshot').then(s=>s.servers[0].pinned===false)"), '取消固定未即时保存。')
    const restored = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(restored.servers[0].pinned, false)
    assert.match(await fs.readFile(path.join(isolated, '.ui', 'servers.yaml'), 'utf8'), /pinned: false/)

    const network = await evaluate("window.watcher.invoke('network.snapshot')")
    assert.ok(Array.isArray(network.entries))
    await evaluate("document.querySelector('#network-nav').click()")
    await wait(() => evaluate("!!document.querySelector('#network-view')"), '网络详情页面未打开。')
    await evaluate("document.querySelector('#settings-nav').click()")
    await wait(() => evaluate("!!document.querySelector('[data-settings=ai]')"), '全局设置导航未恢复。')
    await evaluate("document.querySelector('[data-settings=ai]').click()")
    await wait(() => evaluate("!!document.querySelector('#ai-form')"), 'AI设置表单未打开。')
    assert.deepEqual(await evaluate("[...document.querySelector('#ai-tier').options].map(o=>o.value)"), ['flex', 'default'])
    const checkbox = await evaluate(`(() => {
      const input = document.querySelector('#ai-auto')
      const label = input.closest('.check-label')
      const text = label.querySelector('span')
      const box = input.getBoundingClientRect(), parent = label.getBoundingClientRect(), words = text.getBoundingClientRect()
      return { inputWidth:box.width, labelWidth:parent.width, labelHeight:parent.height,
        textWidth:words.width, gap:words.left-box.right, overflow:words.right-parent.right }
    })()`)
    assert.ok(checkbox.inputWidth >= 12 && checkbox.inputWidth < 30, JSON.stringify(checkbox))
    assert.ok(checkbox.labelWidth >= 350 && checkbox.labelHeight < 80, JSON.stringify(checkbox))
    assert.ok(checkbox.textWidth >= 150 && checkbox.gap >= 0 && checkbox.gap <= 16 && checkbox.overflow <= 1, JSON.stringify(checkbox))
    const ai = await evaluate("window.watcher.invoke('ai.settings')")
    assert.ok(!Object.hasOwn(ai, 'key') && !Object.hasOwn(ai, 'api_key'))
    assert.equal(await evaluate("window.watcher.invoke('arbitrary.execute', {}).then(()=>false,e=>e.message.includes('未知'))"), true)
    assert.equal(await evaluate("window.watcher.invoke('icon.import', {path:'C:/invalid-secret.ico'}).then(()=>false,e=>e.message.includes('文件对话框'))"), true)
    assert.equal(await evaluate("fetch('autogpu://app/config.yaml').then(()=>false,()=>true)"), true)

    await send('Emulation.setDeviceMetricsOverride', { width: 900, height: 660, deviceScaleFactor: 1, mobile: false })
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
    await send('Emulation.clearDeviceMetricsOverride')
    if (process.argv.includes('--screenshots')) {
      const capture = async filename => {
        await pause(100)
        const shot = await send('Page.captureScreenshot', { format: 'png' })
        await fs.writeFile(path.join(isolated, filename), Buffer.from(shot.result.data, 'base64'))
      }
      await capture('settings-ai-light.png')
      // 合成快照只在CDP里展示，Python后台仍是上面已验证的隔离真实实例。
      await evaluate(`(() => {
        state.refreshing = true
        state.networkAutomatic = false
        state.snapshot = structuredClone(state.snapshot)
        state.snapshot.platform.hosts = [{entry:'autodl-demo-1 · 合成展示',slots:'1/2',state:'正常',rows:[
          {gpu_index:0,memory_pct:72,util_pct:98,used_mb:23593,total_mb:32768,observed_at:new Date().toISOString(),state:'正常'},
          {gpu_index:1,memory_pct:18,util_pct:12,used_mb:5898,total_mb:32768,observed_at:new Date().toISOString(),state:'正常'}]}]
        const server = state.snapshot.servers[0], now = Date.now()/1000
        server.name='隔离服务器 · 合成展示'
        server.pinned=true
        server.status={active:true,busy:false,closing:false,interval:120,samples:18,attempts:1}
        server.snapshot={received_at:now,user:'fixture',gpus:[{index:0,memory_used:23593,memory_total:32768,util:98,temperature:66}],
          gpu_processes:[{gpu_index:0,pid:5120,user:'fixture',memory_used:22000,process:'experiment.py',config:'示例实验',started_at:now-7200}],
          cards:[{key:'fixture-task',name:'重构展示实验',pid:5120,user:'fixture',alive:true,level:'success',status:'正在训练',
            advanced_at:now-20,started_at:now-7200,last_alive_at:now,gpu_indices:[0],gpu_memory_mb:22000,tmux_session:'demo-session',
            rule_status:'专用规则已启用 · 合成展示',progress:{epoch:8,total_epochs:30,step:7200,total_steps:21600,
              train:{epoch:8,step:7200,metrics:{loss:.0834,auc:.9721}},tests:{validation:{epoch:7,step:6300,metrics:{auc:.9845,ce:.1548}}}}}]}
        state.snapshot.logs=[{level:'success',text:'隔离视觉展示：GPU采样正常，数据为合成快照'},
          {level:'normal',text:'SSH实际采集未启动，展示进程与训练卡片的排版'}]
        state.snapshot.theme='dark'
        applyTheme('dark')
        navigate('platform')
      })()`)
      await capture('platform-dark.png')
      await evaluate("state.snapshot.theme='light'; applyTheme('light'); state.renderingKey=''; render()")
      await capture('platform-light.png')
      await evaluate("state.snapshot.theme='dark'; applyTheme('dark')")
      await evaluate('navigate(state.snapshot.servers[0].id)')
      await capture('training-dark.png')
      await evaluate("state.snapshot.theme='light'; applyTheme('light'); state.renderingKey=''; render()")
      await capture('training-light.png')
      await evaluate("state.snapshot.theme='dark'; applyTheme('dark'); state.settingsTab='monitor'; navigate('settings')")
      await capture('settings-dark.png')
      await evaluate("state.snapshot.theme='light'; applyTheme('light'); state.renderingKey=''; render()")
      await capture('settings-light.png')
      await evaluate("state.snapshot.theme='dark'; applyTheme('dark'); navigate('network')")
      await wait(() => evaluate('!state.networkBusy'), '网络截图的只读快照未完成。')
      await capture('network-dark.png')
      await evaluate("state.snapshot.theme='light'; applyTheme('light'); state.renderingKey=''; render()")
      await capture('network-light.png')
    }
    await evaluate("window.watcher.invoke('desktop.close')")
    await Promise.race([windowClosed, pause(10000).then(() => {
      if (socket.readyState !== WebSocket.CLOSED) throw new Error('窗口关闭后桌面调试连接仍在运行。')
    })])
    await Promise.race([exited, pause(10000).then(() => {
      if (processHandle.exitCode === null) throw new Error('关闭后源码入口未正常退出。')
    })])
    assert.equal(processHandle.exitCode, 0)
    const trace = await wait(async () => {
      try { return JSON.parse(await fs.readFile(networkLog, 'utf8')) } catch { return null }
    }, 'Electron退出后网络日志未完整保存。')
    const remoteRequests = trace.events.filter(event => /^https?:\/\//.test(event.params?.url || ''))
    assert.equal(remoteRequests.length, 0, '隔离无账号界面出现额外HTTP访问，请核对Chromium词典初始化。')
    succeeded = true
    console.log((launcherMode ? '轻量EXE' : sourceMode ? '默认源码' : 'Electron') + '入口、真实窗口、preload隔离、Python IPC、YAML保存、服务器固定、网络快照及正常退出通过。')
  } finally {
    if (processHandle.exitCode === null || socket?.readyState === WebSocket.OPEN) {
      if (evaluate) await evaluate("window.watcher.invoke('desktop.close')").catch(() => {})
      await Promise.race([exited, pause(5000)])
      if (processHandle.exitCode === null) processHandle.kill()  // 只回收本测试创建的窗口，后台由EOF收尾。
    }
    socket?.close()
    for (const junction of junctions) {
      const information = await fs.lstat(junction.path)
      assert.equal(information.isSymbolicLink(), true, '临时源码入口已被替换，停止清理。')
      assert.equal(path.normalize(await fs.realpath(junction.path)), path.normalize(junction.target))
      await fs.unlink(junction.path)  // 先解除已核对的目录联接，再递归删除临时资料目录。
    }
    // EXE启动的Conda/Python父进程可能比窗口晚片刻释放工作目录，等待Windows句柄正常回收。
    if (succeeded && !process.argv.includes('--screenshots')) await fs.rm(isolated, { recursive: true, maxRetries: 10, retryDelay: 100 })
    else console.log('隔离验收资料目录 ' + isolated)
  }
}

run().catch(error => { console.error(error); process.exitCode = 1 })
