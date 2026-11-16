// 安装验收共用真实窗口连接与合成资料。所有文件都限定在installer/dist内。
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { spawn, spawnSync } = require('node:child_process')
const { createHash } = require('node:crypto')

const root = path.resolve(__dirname, '..')
const stage = path.join(root, 'installer', 'dist', 'verification')
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

function ensureWebSocket() {
  if (typeof WebSocket !== 'undefined') return
  const runtime = path.join(root, 'desktop', 'node_modules', 'electron', 'dist', 'autodl-watcher-desktop.exe')
  const result = spawnSync(runtime, [process.argv[1], ...process.argv.slice(2)], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, stdio: 'inherit', windowsHide: true,
  })
  if (result.error) throw result.error
  process.exit(result.status ?? 1)
}

async function wait(check, label, timeout = 120000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const result = await check()
    if (result) return result
    await pause(250)
  }
  throw new Error(label)
}

function checkedTestPath(value) {
  const result = path.resolve(value)
  const relative = path.relative(path.join(root, 'installer', 'dist'), result)
  assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), '验收资料须在installer/dist内')
  return result
}

async function hash(file) {
  return createHash('sha256').update(await fs.readFile(file)).digest('hex')
}

async function prepareProfile(profile) {
  checkedTestPath(profile)
  await fs.mkdir(path.join(profile, '.ui'), { recursive: true })
  await fs.copyFile(path.join(root, 'config.yaml'), path.join(profile, 'config.yaml'))
  await fs.writeFile(path.join(profile, '.ui', 'preferences.yaml'),
    'user: installer-fixture\npoll: "120"\nlive: false\nconvert_no_gpu: false\ncustom_history: keep\nui:\n  theme: dark\n')
  await fs.writeFile(path.join(profile, '.ui', 'servers.yaml'),
    '- id: installer-fixture\n  name: 安装验收服务器\n  ssh_alias: example-fixture\n  pinned: true\n')
  await fs.writeFile(path.join(profile, '.ui', 'training_rules.yaml'), '{}\n')
  const { DatabaseSync } = require('node:sqlite')
  const database = new DatabaseSync(path.join(profile, '.ui', 'ai_usage.sqlite3'))
  database.exec('CREATE TABLE calls (time REAL NOT NULL, record TEXT NOT NULL)')
  const record = { time: Date.now() / 1000, model: 'fixture-model', requested_tier: 'flex', actual_tier: 'flex',
    status: 'completed', tokens: { input: 100, output: 20, cached: 0, write: 0 },
    usd: .01, cny: .07, cost_note: '安装保留验收样例' }
  database.prepare('INSERT INTO calls VALUES (?, ?)').run(record.time, JSON.stringify(record))
  database.close()
}

async function openInstalled(executable, profile, extra = []) {
  assert.ok(['autodl-desktop.exe', 'autodl-update-test.exe'].includes(path.basename(executable)), '安装版程序名不符')
  profile = checkedTestPath(profile)
  const local = path.join(profile, 'test-local-app-data')
  await fs.mkdir(local, { recursive: true })
  const env = { ...process.env, LOCALAPPDATA: local }
  env.NO_PROXY = '127.0.0.1,localhost'
  for (const key of Object.keys(env)) {
    if (/^(CONDA_|PYTHON|AUTODL_|ELECTRON_RUN_AS_NODE|VIRTUAL_ENV)/i.test(key)) delete env[key]
  }
  for (const key of Object.keys(env)) if (key.toLowerCase() === 'path') delete env[key]
  env.Path = [path.join(env.SystemRoot, 'System32'), env.SystemRoot].join(path.delimiter)
  const portFile = path.join(profile, '.ui', 'electron', 'DevToolsActivePort')
  await fs.rm(portFile, { force: true })
  const child = spawn(executable, ['--autodl-data-root', profile, '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0', ...extra], { env, cwd: path.dirname(executable), stdio: 'ignore', windowsHide: false })
  let exited = false
  let socket
  child.once('exit', () => { exited = true })
  try {
    const port = await wait(async () => {
      if (exited) throw new Error('安装版窗口提前退出，状态码' + child.exitCode)
      const value = await fs.readFile(portFile, 'utf8').catch(error => {
        if (['ENOENT', 'EBUSY'].includes(error.code)) return ''  // Chrome正在创建端口文件时，下一轮再读。
        throw error
      })
      return value.split('\n')[0].trim()
    }, '安装版未生成本机调试端口')
    const page = await wait(async () => {
      const response = await fetch('http://127.0.0.1:' + port + '/json/list').catch(() => null)
      return response?.ok && (await response.json()).find(item => item.url === 'autogpu://app/index.html')
    }, '安装版界面未加载')
    socket = new WebSocket(page.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true })
      socket.addEventListener('error', reject, { once: true })
    })
    let serial = 0
    const pending = new Map()
    socket.addEventListener('message', event => {
      const response = JSON.parse(event.data)
      const request = pending.get(response.id)
      if (!request) return
      pending.delete(response.id)
      clearTimeout(request.timer)
      request.resolve(response)
    })
    const send = (method, params) => new Promise((resolve, reject) => {
      const id = ++serial
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('窗口调用超时' + method)) }, 15000)
      pending.set(id, { resolve, reject, timer })
      socket.send(JSON.stringify({ id, method, params }))
    })
    const evaluate = async expression => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      assert.ok(!response.error && !response.result.exceptionDetails, JSON.stringify(response))
      return response.result.result.value
    }
    await wait(() => evaluate("!!document.body && typeof window.watcher?.invoke==='function' && typeof state!=='undefined' && !!state.snapshot"), '安装版后台未就绪')
    return { child, socket, send, evaluate, env, profile,
      async close() {
        await evaluate("window.watcher.invoke('desktop.close')")
        await wait(() => exited, '安装版未正常退出')
        assert.equal(child.exitCode, 0)
        socket.close()
      } }
  } catch (error) {
    socket?.close()
    // 尚未返回给调用者，未发起任何监控操作，只回收本函数创建的启动失败实例。
    if (!exited) child.kill()
    await wait(() => exited, '启动失败实例未退出', 5000)
    throw error
  }
}

module.exports = { root, stage, pause, wait, ensureWebSocket, checkedTestPath, hash, prepareProfile, openInstalled }
