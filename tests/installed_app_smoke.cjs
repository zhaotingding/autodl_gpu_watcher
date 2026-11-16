const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const support = require('./installed_test_support.cjs')
support.ensureWebSocket()

async function run() {
  const executable = path.resolve(process.argv[2] || '')
  assert.ok(process.argv[2], '用法 node tests/installed_app_smoke.cjs <安装后的EXE> [隔离资料根]')
  await fs.mkdir(support.stage, { recursive: true })
  const profile = process.argv[3] ? support.checkedTestPath(process.argv[3])
    : await fs.mkdtemp(path.join(support.stage, 'profile-'))
  if (!process.argv[3]) await support.prepareProfile(profile)
  const immutable = ['training_rules.yaml', 'ai_usage.sqlite3']
  const before = await Promise.all(immutable.map(name => support.hash(path.join(profile, '.ui', name))))
  const application = await support.openInstalled(executable, profile, ['--autodl-open-setup'])
  try {
    const { evaluate } = application
    const snapshot = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(path.normalize(snapshot.app.root), path.normalize(profile))
    assert.equal(path.normalize(snapshot.app.python), path.normalize(path.join(path.dirname(executable), 'resources/backend/autodl-backend.exe')))
    assert.equal(snapshot.preferences.user, 'installer-fixture')
    assert.equal(snapshot.servers[0].name, '安装验收服务器')
    assert.equal(snapshot.worker.running, false)
    await support.wait(() => evaluate("document.querySelector('#setup-dialog')?.open"), '手动安装基础设置未打开')
    await evaluate("document.querySelector('#setup-skip').click()")
    await support.wait(() => evaluate("!document.querySelector('#setup-dialog')?.open"), '基础设置跳过未完成')
    const usage = await evaluate("window.watcher.invoke('ai.usage')")
    assert.ok(usage.summary.includes('fixture-model') && usage.summary.includes('100'), usage.summary)
    await evaluate("window.watcher.invoke('preferences.save',{ui:{theme:'light'}})")
    const latest = await evaluate("window.watcher.invoke('snapshot')")
    assert.equal(latest.theme, 'light')
    assert.equal(latest.preferences.user, 'installer-fixture')
    await application.close()
    const after = await Promise.all(immutable.map(name => support.hash(path.join(profile, '.ui', name))))
    assert.deepEqual(after, before)
    console.log('安装版随包后台、独立启动、基础设置跳过、原服务器与规则/账本保留通过。')
    console.log('验收资料 ' + profile)
  } finally {
    if (application.child.exitCode === null) await application.close()
  }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
