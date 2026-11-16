// 对已安装的独立测试产品执行一次真实回环更新，读回原规则和Token账本。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const fsp = require('node:fs/promises')
const http = require('node:http')
const path = require('node:path')
const { execFileSync } = require('node:child_process')
const support = require('./installed_test_support.cjs')
support.ensureWebSocket()

async function run() {
  const [programArgument, feedArgument] = process.argv.slice(2)
  assert.ok(programArgument && feedArgument, '用法 node tests/installed_update_smoke.cjs <测试安装EXE> <新版构建目录>')
  const executable = await fsp.realpath(path.resolve(programArgument))
  assert.equal(path.basename(executable), 'autodl-update-test.exe')
  const feed = support.checkedTestPath(feedArgument)
  const configuration = await fsp.readFile(path.join(path.dirname(executable), 'resources', 'app-update.yml'), 'utf8')
  const address = /^url:\s*['"]?(http:\/\/127\.0\.0\.1:\d+\/)['"]?\s*$/m.exec(configuration)?.[1]
  assert.ok(address && /^provider:\s*generic\s*$/m.test(configuration), '只允许本机隔离更新源')
  const latest = await fsp.readFile(path.join(feed, 'latest.yml'), 'utf8')
  const version = /^version:\s*['"]?(\d+\.\d+\.\d+)['"]?\s*$/m.exec(latest)?.[1]
  const asset = 'autodl-update-test-' + version + '-x64.exe'
  assert.ok(version && latest.includes(asset))
  await fsp.access(path.join(feed, asset))
  await fsp.mkdir(support.stage, { recursive: true })
  const profile = await fsp.mkdtemp(path.join(support.stage, 'update-profile-'))
  await support.prepareProfile(profile)
  const preserved = ['training_rules.yaml', 'ai_usage.sqlite3', 'servers.yaml']
  const before = await Promise.all(preserved.map(name => support.hash(path.join(profile, '.ui', name))))
  const requests = []
  let corruptManifest = true
  const server = http.createServer(async (request, response) => {
    const name = decodeURIComponent(new URL(request.url, address).pathname.split('/').at(-1))
    requests.push(name)
    if (!['latest.yml', asset, asset + '.blockmap'].includes(name)) { response.writeHead(404).end(); return }
    const file = path.join(feed, name)
    const info = await fsp.stat(file).catch(error => {
      if (error.code === 'ENOENT') return null
      throw error
    })
    if (!info) { response.writeHead(404).end(); return }
    if (name === 'latest.yml' && corruptManifest) {
      const incorrect = latest.replace(/sha512:\s*\S+/g, 'sha512: ' + Buffer.alloc(64, 7).toString('base64'))
      response.writeHead(200, { 'Content-Length': Buffer.byteLength(incorrect), 'Content-Type': 'text/yaml', 'Cache-Control': 'no-store' })
      response.end(incorrect)
      return
    }
    response.writeHead(200, { 'Content-Length': info.size, 'Content-Type': name.endsWith('.yml') ? 'text/yaml' : 'application/octet-stream' })
    if (request.method === 'HEAD') response.end()
    else fs.createReadStream(file).on('error', error => response.destroy(error)).pipe(response)
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(Number(new URL(address).port), '127.0.0.1', resolve)
  })
  let application
  try {
    application = await support.openInstalled(executable, profile, ['--updated'])
    const initial = await application.evaluate("window.watcher.invoke('snapshot')")
    assert.notEqual(initial.app.version, version)
    await support.wait(() => application.evaluate("window.watcher.invoke('desktop.update.status').then(s=>s.phase==='available')"), '启动后未自动发现新版本', 45000)
    assert.ok(!requests.includes(asset), '用户点击前开始下载了安装包')
    assert.equal(await application.evaluate("!document.querySelector('#app-menu-help .update-dot').hidden"), true)
    await application.evaluate("window.watcher.invoke('desktop.update.check')")
    // 打开与帮助菜单共用的更新对话框，再通过实际按钮开始下载。
    await application.evaluate("checkUpdates()")
    await support.wait(() => application.evaluate("document.querySelector('#update-dialog')?.open && !!document.querySelector('#update-install')"), '更新对话框未显示')
    await application.evaluate("document.querySelector('#update-install').click()")
    await support.wait(() => application.evaluate("window.watcher.invoke('desktop.update.status').then(s=>s.phase==='error')"), '摘要错误未拒绝安装', 180000)
    assert.equal(application.child.exitCode, null, '摘要错误关闭了旧应用')
    assert.equal((await application.evaluate("window.watcher.invoke('snapshot')")).app.version, initial.app.version)
    console.log('错误摘要已实际拒绝，旧版窗口与后台仍可使用。')
    corruptManifest = false
    await application.evaluate("checkUpdates()")
    await application.evaluate("document.querySelector('#update-install').click()")
    await support.wait(() => application.child.exitCode === 0, '旧版未在更新前正常退出', 240000)
    const info = await support.wait(() => {
      const raw = execFileSync(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        ['-NoProfile', '-NonInteractive', '-Command', [
          '[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)',
          '$item=Get-Item -LiteralPath $env:AUTODL_VERIFY_EXE -ErrorAction SilentlyContinue',
          '$running=@(Get-Process -Name autodl-update-test -ErrorAction SilentlyContinue | Where-Object {$_.Path -eq $env:AUTODL_VERIFY_EXE -and $_.MainWindowHandle -ne 0} | Select-Object -ExpandProperty Id)',
          `$profiles=@(Get-CimInstance Win32_Process -Filter "Name = 'autodl-update-test.exe'" | Where-Object {$_.ParentProcessId -in $running -and $_.CommandLine -match '--type=renderer'} | ForEach-Object {if ($_.CommandLine -match '--user-data-dir=(?:"([^"]+)"|(\\S+))') {if ($Matches[1]) {$Matches[1]} else {$Matches[2]}}})`,
          '[pscustomobject]@{version=$item.VersionInfo.ProductVersion;running=$running;profiles=$profiles} | ConvertTo-Json -Compress',
        ].join('\n')], { encoding: 'utf8', windowsHide: true, env: { ...process.env, AUTODL_VERIFY_EXE: executable } })
      const result = JSON.parse(raw)
      result.version = /^(\d+\.\d+\.\d+)(?:\.0)?$/.exec(result.version)?.[1] || ''
      return result.version === version && result.running.length ? result : null
    }, '新版本未从原程序位置重新打开', 180000)
    assert.ok(info.profiles.some(value => path.normalize(value) === path.normalize(path.join(profile, '.ui', 'electron'))),
      '自动重开未沿用实际资料根，不能用后续显式参数重开代替此验收')
    assert.ok(requests.includes(asset), '回环源未收到安装包下载请求')
    application.socket.close()
    // 旧实例调试端口不会传给新版，先正常关闭更新重开的该安装实例，再以调试参数读回原资料。
    execFileSync(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'),
      ['-NoProfile', '-NonInteractive', '-Command', '$p=Get-Process -Id $env:AUTODL_VERIFY_PID; [void]$p.CloseMainWindow(); $p.WaitForExit(30000)'],
      { encoding: 'utf8', windowsHide: true, env: { ...process.env, AUTODL_VERIFY_PID: String(info.running[0]) } })
    application = await support.openInstalled(executable, profile, ['--updated'])
    const afterState = await application.evaluate("window.watcher.invoke('snapshot')")
    assert.equal(afterState.app.version, version)
    assert.equal(path.normalize(afterState.app.root), path.normalize(profile))
    assert.equal(afterState.preferences.user, 'installer-fixture')
    assert.equal(afterState.servers[0].name, '安装验收服务器')
    const ledger = await application.evaluate("window.watcher.invoke('ai.usage')")
    assert.ok(ledger.summary.includes('fixture-model'))
    await application.close()
    const after = await Promise.all(preserved.map(name => support.hash(path.join(profile, '.ui', name))))
    assert.deepEqual(after, before)
    console.log('真实双版本更新通过 ' + initial.app.version + ' → ' + version + '，先提示后下载、自动安装重开、资料与账本读回。')
    console.log('更新源请求 ' + requests.join(', '))
    console.log('隔离资料 ' + profile)
  } finally {
    application?.socket.close()
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
