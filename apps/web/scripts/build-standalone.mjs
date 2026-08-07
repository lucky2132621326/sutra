/**
 * Bundle the cockpit into ONE self-contained HTML file.
 *
 * Two reasons this exists:
 *  - Demo insurance. If the venue network, the laptop, or the backend fails,
 *    the recorded runs still play from a single file on a USB stick or a URL.
 *  - Review. It can be hosted and opened on any screen, which is how the
 *    layout actually gets judged.
 *
 * Replay only, by construction: there is no backend to talk to, so the live
 * controls simply report the backend as down.
 *
 *     node scripts/build-standalone.mjs   ->  dist-standalone/sutra.html
 */
import { execSync } from 'node:child_process'
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = dirname(dirname(fileURLToPath(import.meta.url)))
const DIST = join(WEB, 'dist')
const OUT_DIR = join(WEB, 'dist-standalone')

console.log('building…')
execSync('npx vite build', { cwd: WEB, stdio: 'inherit' })

const html = readFileSync(join(DIST, 'index.html'), 'utf8')
const assets = readdirSync(join(DIST, 'assets'))

const js = assets.filter((f) => f.endsWith('.js'))
const css = assets.filter((f) => f.endsWith('.css'))
if (js.length !== 1) {
  throw new Error(`expected exactly one JS chunk to inline, found ${js.length}: ${js.join(', ')}`)
}

const fixtures = {}
for (const f of readdirSync(join(WEB, 'public', 'fixtures'))) {
  if (f.endsWith('.jsonl')) {
    fixtures[f] = readFileSync(join(WEB, 'public', 'fixtures', f), 'utf8')
  }
}
console.log(`inlining ${Object.keys(fixtures).length} fixtures`)

// </script> anywhere inside the payload would close the tag early.
const fixturesJson = JSON.stringify(fixtures).replace(/<\//g, '<\\/')

const inlineCss = css.map((f) => `<style>${readFileSync(join(DIST, 'assets', f), 'utf8')}</style>`).join('\n')
const inlineJs = readFileSync(join(DIST, 'assets', js[0]), 'utf8')

// NOTE: every replacement below is a FUNCTION, never a string. A string
// replacement has `$&`, `$'` and friends interpreted as substitution patterns,
// and a minified bundle contains those byte sequences — which silently spliced
// large slices of the document back into itself and produced a 1.3 MB file
// where 0.5 MB was expected, with duplicated <style> blocks throughout.
let out = html
  .replace(/<script[^>]*src="[^"]*"[^>]*><\/script>/g, () => '')
  .replace(/<link[^>]*rel="stylesheet"[^>]*>/g, () => '')
  .replace('</head>', () => `${inlineCss}\n</head>`)
  .replace(
    '</body>',
    () =>
      `<script>window.__SUTRA_FIXTURES__=${fixturesJson};</script>\n` +
      `<script type="module">${inlineJs}</script>\n</body>`,
  )

/** Assert the document contains each asset exactly once and nothing dangling. */
function check(name, doc, { expectRoot = true, extraStyles = 0 } = {}) {
  const styles = (doc.match(/<style>/g) ?? []).length
  const bundles = doc.split(inlineJs.slice(0, 400)).length - 1
  const dangling = /(src|href)="\/assets\//.test(doc)
  const problems = []
  const wantStyles = css.length + extraStyles
  if (styles !== wantStyles) problems.push(`${styles} <style> blocks, expected ${wantStyles}`)
  if (bundles !== 1) problems.push(`bundle appears ${bundles}x, expected once`)
  if (dangling) problems.push('still references /assets/ — an asset was not inlined')
  if (expectRoot && !doc.includes('id="root"')) problems.push('no #root to mount into')
  if (problems.length) throw new Error(`${name}: ${problems.join('; ')}`)
  console.log(`  ${name}: ok — 1 bundle, ${styles} style block(s), nothing external`)
}

mkdirSync(OUT_DIR, { recursive: true })
const outPath = join(OUT_DIR, 'sutra.html')
check('sutra.html', out)
writeFileSync(outPath, out, 'utf8')

// A second, scaffolding-free variant for hosts that supply their own
// <html>/<head>/<body> and inject this as page content.
const fragment = [
  `<title>Sūtra — Smart Campus Orchestrator</title>`,
  inlineCss,
  // The app fills the viewport; a host page's default body flow would
  // collapse it to zero height.
  `<style>html,body{height:100%;margin:0}#root{height:100vh}</style>`,
  `<div id="root"></div>`,
  `<script>window.__SUTRA_FIXTURES__=${fixturesJson};</script>`,
  `<script type="module">${inlineJs}</script>`,
].join('\n')
const fragPath = join(OUT_DIR, 'sutra-embed.html')
// +1 for the host-page sizing rules this variant adds.
check('sutra-embed.html', fragment, { extraStyles: 1 })
writeFileSync(fragPath, fragment, 'utf8')

const mb = (n) => (Buffer.byteLength(n) / 1024 / 1024).toFixed(2)
console.log(`\n  ${outPath}  (${mb(out)} MB)  — open directly, no server`)
console.log(`  ${fragPath}  (${mb(fragment)} MB)  — for embedding hosts`)
