const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')
const { installedProfile } = require('../installer/installed-profile.cjs')

function fixture(t) {
  // 每项测试单独使用合成资料根，不读取当前用户资料。
  const tempRoot = path.resolve(os.tmpdir())
  const root = fs.mkdtempSync(path.join(tempRoot, 'autodl-profile-test-'))
  const previousLocalAppData = process.env.LOCALAPPDATA
  const previousArgv = process.argv
  const localAppData = path.join(root, 'LocalAppData')
  const resources = path.join(root, 'installed', 'resources')
  const defaultRoot = path.join(localAppData, 'autodl-watcher', 'profile')
  process.env.LOCALAPPDATA = localAppData
  process.argv = ['autodl-desktop.exe']
  t.after(() => {
    process.argv = previousArgv
    if (previousLocalAppData === undefined) delete process.env.LOCALAPPDATA
    else process.env.LOCALAPPDATA = previousLocalAppData
    assert.equal(path.dirname(root), tempRoot)
    assert.match(path.basename(root), /^autodl-profile-test-[A-Za-z0-9]+$/)
    fs.rmSync(root, { recursive: true, force: true })
  })

  function write(file, contents = '') {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, contents)
  }

  function repository() {
    const repo = path.join(root, '原Auto算力项目')
    const installerFolder = path.join(repo, 'installer', 'dist', 'release', 'local')
    fs.mkdirSync(installerFolder, { recursive: true })
    for (const file of ['src/autodl_watcher/__init__.py', 'desktop/package.json', 'installer/build-installer.mjs', 'config.yaml']) {
      write(path.join(repo, file))
    }
    return { repo, installerFolder }
  }

  // NSIS 写入的是 UTF-16LE 程序目录来源，路径可能含中文或空格。
  const recordOrigin = dir => write(path.join(resources, 'installer-origin.txt'), Buffer.from(dir, 'utf16le'))
  const open = (testBuild = false) => installedProfile({ getPath: () => localAppData }, testBuild, resources)
  return { resourceRoot: root, write, repository, recordOrigin, open, defaultRoot }
}

test('仅保存config.yaml的既有安装资料仍优先于源码来源', t => {
  const { repository, recordOrigin, write, open, defaultRoot } = fixture(t)
  const { repo, installerFolder } = repository()
  write(path.join(repo, '.ui', 'preferences.yaml'), 'ui:\n  theme: dark\n')
  write(path.join(defaultRoot, 'config.yaml'), 'occupancy:\n  self_user: existing-user\n')
  recordOrigin(installerFolder)
  assert.equal(open().root, defaultRoot)
})

test('仅有config.yaml的旧wheel资料可以原地继续使用', t => {
  const { resourceRoot, repository, recordOrigin, write, open } = fixture(t)
  const { repo, installerFolder } = repository()
  const legacy = path.join(resourceRoot, 'LocalAppData', 'autodl-watcher')
  write(path.join(legacy, 'config.yaml'), 'occupancy:\n  self_user: existing-wheel-user\n')
  write(path.join(repo, '.ui', 'preferences.yaml'), 'ui:\n  theme: dark\n')
  recordOrigin(installerFolder)
  assert.equal(open().root, legacy)
})

test('显式资料根和已有资料选择优先于安装包来源及默认资料', t => {
  const { resourceRoot, repository, recordOrigin, write, open, defaultRoot } = fixture(t)
  const { repo, installerFolder } = repository()
  const previousRoot = path.join(resourceRoot, '之前指定的资料目录')
  write(path.join(previousRoot, '.ui', 'preferences.yaml'), 'theme: light\n')
  write(path.join(repo, '.ui', 'preferences.yaml'), 'theme: dark\n')
  write(path.join(defaultRoot, '.ui', 'ai_usage.sqlite3'), 'default history')
  recordOrigin(installerFolder)

  process.argv.push('--autodl-data-root', previousRoot)
  const selected = open()
  assert.equal(selected.root, previousRoot)
  selected.saveSelection()
  process.argv = ['autodl-desktop.exe']
  assert.equal(open().root, previousRoot)
  assert.equal(fs.readFileSync(path.join(defaultRoot, '.ui', 'ai_usage.sqlite3'), 'utf8'), 'default history')
})

for (const file of ['.ui/preferences.yaml', 'config.local.yaml', '.ui/ai_usage.sqlite3']) {
  test(`默认资料目录中的 ${file} 优先于源码目录`, t => {
    const { repository, recordOrigin, write, open, defaultRoot } = fixture(t)
    const { repo, installerFolder } = repository()
    write(path.join(repo, '.ui', 'preferences.yaml'), 'theme: dark\n')
    write(path.join(defaultRoot, file), 'existing data')
    recordOrigin(installerFolder)

    assert.equal(open().root, defaultRoot)
    assert.equal(fs.readFileSync(path.join(defaultRoot, file), 'utf8'), 'existing data')
  })
}

test('直接从源码项目安装时默认原地沿用资料并记住路径', t => {
  const { repository, recordOrigin, write, open, defaultRoot } = fixture(t)
  const { repo, installerFolder } = repository()
  const prefs = path.join(repo, '.ui', 'preferences.yaml')
  const history = path.join(repo, '.ui', 'ai_usage.sqlite3')
  write(prefs, 'theme: light\n')
  write(history, 'source history')
  write(path.join(defaultRoot, '.ui', 'electron', 'Cache', 'sample'), 'browser cache')
  recordOrigin(installerFolder)

  const selected = open()
  assert.equal(selected.root, repo)
  selected.saveSelection()
  recordOrigin(path.dirname(repo))
  assert.equal(open().root, repo)
  assert.equal(fs.readFileSync(prefs, 'utf8'), 'theme: light\n')
  assert.equal(fs.readFileSync(history, 'utf8'), 'source history')
  assert.equal(fs.existsSync(path.join(defaultRoot, '.ui', 'ai_usage.sqlite3')), false)
})

test('来源不在项目 installer 目录下时使用默认资料目录', t => {
  const { repository, recordOrigin, write, open, defaultRoot } = fixture(t)
  const { repo } = repository()
  const download = path.join(repo, 'downloads')
  fs.mkdirSync(download)
  write(path.join(repo, '.ui', 'ai_usage.sqlite3'), 'outside installer history')
  recordOrigin(download)

  assert.equal(open().root, defaultRoot)
})

test('普通文件夹仅含同名 installer 目录和偏好时使用默认资料目录', t => {
  const { resourceRoot, recordOrigin, write, open, defaultRoot } = fixture(t)
  const other = path.join(resourceRoot, '外部目录')
  const download = path.join(other, 'installer', 'packages')
  fs.mkdirSync(download, { recursive: true })
  write(path.join(other, '.ui', 'preferences.yaml'), 'theme: dark\n')
  recordOrigin(download)

  assert.equal(open().root, defaultRoot)
})

test('验收身份只使用隔离资料，首次设置完成状态按身份保留', t => {
  const { repository, recordOrigin, write, open } = fixture(t)
  const { repo, installerFolder } = repository()
  const prefs = path.join(repo, '.ui', 'preferences.yaml')
  write(prefs, 'theme: light\n')
  recordOrigin(installerFolder)

  const production = open()
  const updateTest = open(true)
  assert.equal(production.root, repo)
  assert.match(updateTest.root, /[\\/]autodl-watcher-update-test[\\/]profile$/)
  assert.equal(production.setupPending(), true)
  assert.equal(updateTest.setupPending(), true)

  production.completeSetup()
  assert.equal(open().setupPending(), false)
  assert.equal(open(true).setupPending(), true)
  updateTest.completeSetup()
  assert.equal(open(true).setupPending(), false)
  assert.equal(fs.readFileSync(prefs, 'utf8'), 'theme: light\n')
})

test('已有wheel资料根在base本身时原地沿用并记住选择', t => {
  const { resourceRoot, repository, recordOrigin, write, open } = fixture(t)
  const { repo, installerFolder } = repository()
  const oldWheel = path.join(resourceRoot, 'LocalAppData', 'autodl-watcher')
  const original = path.join(oldWheel, '.ui', 'ai_usage.sqlite3')
  write(original, 'existing wheel ledger')
  write(path.join(repo, '.ui', 'preferences.yaml'), 'theme: dark\n')
  recordOrigin(installerFolder)

  const selected = open()
  assert.equal(selected.root, oldWheel)
  selected.saveSelection()
  assert.equal(open().root, oldWheel)
  assert.equal(fs.readFileSync(original, 'utf8'), 'existing wheel ledger')
})

test('个人资料不可选择到程序或其resources之内', t => {
  const { resourceRoot, write, open } = fixture(t)
  const program = path.join(resourceRoot, 'installed')
  write(path.join(program, '.ui', 'preferences.yaml'), 'theme: dark\n')
  process.argv.push('--autodl-data-root', program)
  assert.throws(() => open(), /程序安装目录之外/)
  process.argv[process.argv.length - 1] = path.join(program, 'resources')
  assert.throws(() => open(), /程序安装目录之外/)
})

test('保存的资料指针损坏时报告错误并保留原文件', t => {
  const { resourceRoot, write, open } = fixture(t)
  const selection = path.join(resourceRoot, 'LocalAppData', 'autodl-watcher', 'profile-path.yaml')
  write(selection, '[invalid pointer')
  assert.throws(() => open(), /无法读取已选择的资料目录/)
  assert.equal(fs.readFileSync(selection, 'utf8'), '[invalid pointer')
})
