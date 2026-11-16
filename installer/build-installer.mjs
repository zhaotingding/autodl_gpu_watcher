import fs from 'node:fs'
import path from 'node:path'
import { parseArgs } from 'node:util'
import { createRequire } from 'node:module'

const installer = import.meta.dirname
const root = path.dirname(installer)
const desktop = path.join(root, 'desktop')
const require = createRequire(path.join(desktop, 'package.json'))
const { Arch, Platform, build } = require('electron-builder')
const manifest = JSON.parse(fs.readFileSync(path.join(desktop, 'package.json'), 'utf8'))
const { values: options } = parseArgs({ options: {
  'test-feed': { type: 'string' },
  'test-version': { type: 'string' },
  'electron-zip': { type: 'string' },
  'local-preview': { type: 'boolean' },
} })
const test = Boolean(options['test-feed'])
const preview = Boolean(options['local-preview'])
if (preview && (test || options['test-version'])) throw new Error('本地安装验收与双版本更新验收请分别构建。')
const signed = !test && !preview
if (!signed) {
  process.env.CSC_IDENTITY_AUTO_DISCOVERY = 'false'
  for (const key of ['WIN_CSC_LINK', 'WIN_CSC_KEY_PASSWORD', 'CSC_LINK', 'CSC_KEY_PASSWORD']) delete process.env[key]
}
const version = fs.readFileSync(path.join(root, 'src', 'autodl_watcher', '__init__.py'), 'utf8')
  .match(/^__version__\s*=\s*"([^"]+)"/m)?.[1]
if (!version) throw new Error('读取Auto算力源码版本失败。')

let publish = { provider: 'github', owner: 'zhaotingding', repo: 'autodl_gpu_watcher' }
if (test) {
  const feed = new URL(options['test-feed'])
  if (feed.protocol !== 'http:' || feed.hostname !== '127.0.0.1' || !feed.port ||
      feed.username || feed.password || feed.search || feed.hash ||
      !/^\d+\.\d+\.\d+$/.test(options['test-version'] || '')) {
    throw new Error('本地验收仅使用127.0.0.1 HTTP更新源及独立的三段式--test-version。')
  }
  publish = { provider: 'generic', url: feed.href }
} else if (options['test-version']) {
  throw new Error('--test-version必须与--test-feed一起使用。')
}

const appVersion = test ? options['test-version'] : version
const backend = path.join(installer, 'dist', 'backend', 'autodl-backend')
if (!fs.existsSync(path.join(backend, 'autodl-backend.exe'))) {
  throw new Error('请先在仓库根目录用项目Conda环境运行 python -s installer/build_backend.py。')
}
const output = path.join(installer, 'dist', test ? 'test' : 'release', preview ? appVersion + '-project-local' : appVersion)
const icon = path.join(root, 'src', 'autodl_watcher', 'default.ico')
let electronDist
if (options['electron-zip']) {
  electronDist = path.resolve(options['electron-zip'])
  if (fs.statSync(electronDist).isFile()) electronDist = path.dirname(electronDist)
}

// 正式包要求发布者签名，本地验收使用明确标记的产物及独立测试身份。
const config = {
  appId: test ? 'autodl.desktop.update-test' : 'autodl.desktop',
  productName: test ? 'Auto算力与训练调度（更新验收）' : 'Auto算力与训练调度',
  executableName: test ? 'autodl-update-test' : 'autodl-desktop',
  extraMetadata: {
    name: test ? 'autodl-update-test' : manifest.name,
    version: appVersion,
    autodlInstalled: true,
    autodlLocalUpdateTest: test,
    autodlLocalPreview: preview,
  },
  electronVersion: manifest.devDependencies.electron,
  ...(electronDist ? { electronDist } : {}),
  directories: { output, buildResources: path.dirname(icon) },
  files: ['main.cjs', 'preload.cjs', 'index.html', 'renderer.js', 'styles.css',
    { from: installer, to: 'installer', filter: ['update.cjs', 'installed-profile.cjs'] }],
  extraResources: [
    { from: backend, to: 'backend' },
    { from: path.join(root, 'src', 'autodl_watcher', 'defaults.yaml'), to: 'config.yaml' },
    { from: icon, to: 'assets/app-icon.ico' },
    { from: path.join(root, 'README.md'), to: 'README.md' },
  ],
  publish: preview ? null : publish,
  npmRebuild: false,
  forceCodeSigning: signed,
  win: { icon, verifyUpdateCodeSignature: signed },
  nsis: {
    include: path.join(installer, 'installer.nsh'),
    oneClick: false,
    perMachine: false,
    deleteAppDataOnUninstall: false,
    artifactName: preview ? 'autodl-setup-project-${version}-${arch}-unsigned.exe'
      : test ? 'autodl-update-test-${version}-${arch}.exe' : 'autodl-setup-${version}-${arch}.exe',
  },
}

await build({ projectDir: desktop, targets: Platform.WINDOWS.createTarget(['nsis'], Arch.x64),
  publish: 'never', config })

if (!preview && !fs.existsSync(path.join(output, 'latest.yml'))) {
  throw new Error('安装包未生成更新清单latest.yml。')
}
if (signed) {
  const updaterConfig = fs.readFileSync(path.join(output, 'win-unpacked', 'resources', 'app-update.yml'), 'utf8')
  if (!/^publisherName:(?:[ \t]+\S|\r?\n[ \t]*-[ \t]+\S)/m.test(updaterConfig)) {
    throw new Error('正式安装包缺少更新发布者身份。')
  }
}
