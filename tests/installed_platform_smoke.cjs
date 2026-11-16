// 用本机合成平台和Telemetry贯通安装版发现worker、Playwright及GPU解析。
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const http = require('node:http')
const path = require('node:path')
const support = require('./installed_test_support.cjs')
support.ensureWebSocket()

async function run() {
  assert.ok(process.argv[2], '请传入已打包或安装的主程序路径')
  const executable = path.resolve(process.argv[2])
  const requests = []
  const server = http.createServer((request, response) => {
    requests.push(request.method + ' ' + request.url)
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname
    if (request.method === 'GET' && pathname.startsWith('/console/')) {
      const instance = pathname.includes('/instance')
      const endpoint = instance ? '/api/v2/instance/list' : '/api/v2/machine/list'
      const payload = instance ? { tenant_uuid: 'fixture-tenant', page_index: 1, page_size: 10 } : {}
      response.writeHead(200, { 'Content-Type': 'text/html' })
      response.end('<script>fetch(' + JSON.stringify(endpoint) + ',{method:"POST",headers:{"Content-Type":"application/json","Authorization":"fixture-token"},body:JSON.stringify(' + JSON.stringify(payload) + ')});</script>')
      return
    }
    let value
    if (pathname === '/api/v2/machine/list') value = { code: 'Success', data: { list: [{ machine_name: 'autodl-999-1', gpu: { idle: 1, total: 2 } }] } }
    else if (pathname === '/api/v2/instance/list') value = { code: 'Success', data: { list: [], result_total: 0 } }
    else if (pathname === '/telemetry') value = { items: [{ host: 'gpu-999', host_status: 'online', gpu_index: 0,
      name: 'fixture-gpu', util_gpu: 67, mem_used_mb: 8192, mem_total_mb: 32768, received_at: new Date().toISOString() }] }
    else { response.writeHead(404).end(); return }
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify(value))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = 'http://127.0.0.1:' + server.address().port
  await fs.mkdir(support.stage, { recursive: true })
  const profile = await fs.mkdtemp(path.join(support.stage, 'platform-profile-'))
  await support.prepareProfile(profile)
  let config = await fs.readFile(path.join(profile, 'config.yaml'), 'utf8')
  config = config.replaceAll('https://private.autodl.com', address)
    .replace('endpoint: ""', 'endpoint: "' + address + '/telemetry"')
  await fs.writeFile(path.join(profile, 'config.yaml'), config)
  let application
  let lastSnapshot
  try {
    application = await support.openInstalled(executable, profile, ['--updated'])
    await application.evaluate("window.watcher.invoke('monitor.refresh')")
    const result = await support.wait(async () => {
      const snapshot = await application.evaluate("window.watcher.invoke('snapshot')")
      lastSnapshot = snapshot
      if (!snapshot.worker.running && snapshot.session.status !== 'valid') throw new Error(JSON.stringify(snapshot.logs))
      return !snapshot.worker.running && snapshot.platform.hosts[0]?.rows.length ? snapshot : null
    }, '随包平台发现未产生GPU数据', 90000)
    assert.equal(result.session.status, 'valid')
    assert.equal(result.platform.hosts[0].entry, 'autodl-999-1')
    assert.equal(result.platform.hosts[0].rows[0].util_pct, 67)
    assert.equal(result.platform.hosts[0].rows[0].memory_pct, 25)
    assert.equal(result.servers[0].status.active, false)
    assert.ok(requests.some(value => value.startsWith('GET /telemetry')))
    await application.close()
    console.log('安装版真实发现worker、Playwright随包驱动、本机平台API及GPU数据到达通过。')
    console.log('本机请求 ' + requests.join(', '))
  } catch (error) {
    console.error(JSON.stringify({ worker: lastSnapshot?.worker, session: lastSnapshot?.session,
      logs: lastSnapshot?.logs, requests }, null, 2))
    throw error
  } finally {
    if (application?.child.exitCode === null) await application.close()
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
