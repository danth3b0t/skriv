// Build design/skriv-ui.fig in the running OpenPencil app over MCP, then export PNGs headlessly.
//   node design/src/build.mjs                 full build -> design/skriv-ui.fig + design/exports/*.png
//   node design/src/build.mjs --only panel    scratch build of one page group -> /tmp/skriv-scratch/*.png
import { execFileSync } from 'node:child_process'
import { existsSync, renameSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { connect } from './mcp.mjs'
import { MODES, VARS, TOKENS, setVarIds } from './lib.mjs'
import { PAGES } from './pages.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const DESIGN = path.resolve(here, '..')
const only = process.argv.includes('--only') ? process.argv[process.argv.indexOf('--only') + 1] : null
const FIG = only ? `scratch-${only}.fig` : 'skriv-ui.fig'

const c = await connect({ root: DESIGN, env: { OPENPENCIL_MCP_EVAL: '1' } })
const J = (r) => JSON.parse(c.text(r))
const evalJS = async (code, doc) => J(await c.call('eval', { code, document_id: doc }))

const nd = J(await c.call('new_document', {}))
const DOC = nd.target?.documentId || nd.document_id || nd.id || nd.documentId
if (!DOC) throw new Error('no document id: ' + JSON.stringify(nd))
console.log('document', DOC)

// ---------------------------------------------------------------- variables + modes
function rgba(hex) {
  const h = hex.replace('#', '')
  const p = (i) => parseInt(h.slice(i, i + 2), 16) / 255
  return { r: p(0), g: p(2), b: p(4), a: h.length === 8 ? p(6) : 1 }
}
const spec = Object.entries(VARS).map(([name, [key, desc]]) => ({
  name, desc, values: MODES.map((m) => rgba(TOKENS[m][key]))
}))
const setup = await evalJS(`
const g = figma.graph
const col = g.createCollection('Skriv · Omarchy theme')
const modes = ${JSON.stringify(MODES)}
g.renameMode(col.id, col.modes[0].modeId, modes[0])
const modeIds = [col.modes[0].modeId]
for (const m of modes.slice(1)) modeIds.push(g.createMode(col.id, m))
const spec = ${JSON.stringify(spec)}
const ids = {}
for (const s of spec) {
  const variable = g.createVariable(s.name, 'COLOR', col.id, s.values[0])
  variable.description = s.desc
  s.values.forEach((val, i) => figma.setVariableValue(variable.id, modeIds[i], val))
  ids[s.name] = variable.id
}
return { col: col.id, modeIds, ids }
`, DOC)
setVarIds(setup.ids)
const MODE_ID = Object.fromEntries(MODES.map((m, i) => [m, setup.modeIds[i]]))
console.log('variables', Object.keys(setup.ids).length, 'modes', MODES.length)

// ---------------------------------------------------------------- pages
const pages = only ? PAGES.filter((p) => p.key === only) : PAGES
let firstPage = true
for (const page of pages) {
  let pageId
  if (firstPage) {
    const r = await evalJS(`figma.currentPage.name = ${JSON.stringify(page.name)}; return { id: figma.currentPage.id }`, DOC)
    pageId = r.id
    firstPage = false
  } else {
    const r = J(await c.call('create_page', { name: page.name, document_id: DOC }))
    pageId = r.id || r.page_id || r.page?.id
  }
  let y = 0
  let x = 0
  let rowH = 0
  const assigned = []
  for (const item of page.frames()) {
    const jsx = item.jsx.trim()
    let r
    try {
      r = J(await c.call('render', { jsx, document_id: DOC, page_id: pageId, x, y }))
    } catch (e) {
      console.error('render failed:', item.theme, jsx.slice(0, 200))
      throw e
    }
    if (r.error) throw new Error(r.error)
    const b = J(await c.call('node_bounds', { id: r.id, document_id: DOC }))
    const bb = b.bounds || b
    const w = bb.width ?? 1200
    const h = bb.height ?? 800
    assigned.push({ id: r.id, theme: item.theme })
    if (page.layout === 'row') { x += w + 80; rowH = Math.max(rowH, h) } else { y += h + 80 }
    if (page.layout === 'row' && item.breakAfter) { x = 0; y += rowH + 80; rowH = 0 }
    console.log(' ', page.name, '←', r.name, `${Math.round(w)}×${Math.round(h)}`)
  }
  // themes: the root's mode, and any descendant named `theme=<mode>`
  await evalJS(`
const g = figma.graph
const MODE_ID = ${JSON.stringify(MODE_ID)}
const col = ${JSON.stringify(setup.col)}
const items = ${JSON.stringify(assigned)}
let n = 0
const VAR = ${JSON.stringify(setup.ids)}
function bindIcon(node, varId) {
  if (node.type === 'VECTOR' && node.fills && node.fills.length) figma.bindVariable(node.id, 'fills/0/color', varId)
  for (const ch of (node.children || [])) bindIcon(ch, varId)
}
function walk(node) {
  const ic = /^icon:(.+)$/.exec(node.name || '')
  if (ic && VAR[ic[1]]) bindIcon(node, VAR[ic[1]])
  const m = /^theme=(.+)$/.exec(node.name || '')
  if (m && MODE_ID[m[1]]) { g.updateNode(node.id, { variableModes: { [col]: MODE_ID[m[1]] } }); n++ }
  for (const ch of (node.children || [])) walk(ch)
}
for (const it of items) {
  if (it.theme) { g.updateNode(it.id, { variableModes: { [col]: MODE_ID[it.theme] } }); n++ }
  walk(figma.getNodeById(it.id))
}
return { n }
`, DOC)
}

const saved = J(await c.call('save_file', { path: FIG, document_id: DOC }))
console.log('saved', saved.path)
if (only) await c.call('close_file', { document_id: DOC, unsaved: 'discard' })
c.close()

// ---------------------------------------------------------------- PNG exports (headless OpenPencil renderer)
const out = only ? `/tmp/skriv-scratch/${only}` : path.join(DESIGN, 'exports')
execFileSync('node', [path.join(here, 'raster.mjs'), path.join(DESIGN, FIG), out, '2'], { stdio: 'inherit' })
if (only) {
  const keep = `/tmp/skriv-scratch/${only}.fig`
  if (existsSync(path.join(DESIGN, FIG))) renameSync(path.join(DESIGN, FIG), keep)
}
process.exit(0)
