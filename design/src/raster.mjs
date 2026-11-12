// Headless PNG export of a saved OpenPencil .fig with @open-pencil/core (same renderer as `openpencil export`).
// node raster.mjs file.fig outDir [scale] [nameFilterRegex]
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { execSync } from 'node:child_process'

const root = execSync('npm root -g').toString().trim() + '/@open-pencil/mcp/node_modules/@open-pencil/core/dist/'
const fig = await import(pathToFileURL(root + 'io/formats/fig/index.js').href)
const raster = await import(pathToFileURL(root + 'io/formats/raster/index.js').href)

const [file, outDir, scaleArg, filter] = process.argv.slice(2)
const scale = Number(scaleArg || 2)
const buf = await readFile(file)
const graph = await fig.readFigFile(new Blob([buf]))
if (fig.populateAllFigPages) await fig.populateAllFigPages(graph)
// The app stores pre-shaped glyph outlines (derivedTextGlyphs, a Figma-compat cache). Some of them come
// back garbled (glyph ids from a different JetBrains Mono build), so shape every text live from the font.
for (const page of graph.getPages()) {
  const stack = [...page.childIds]
  while (stack.length) {
    const n = graph.getNode(stack.pop())
    if (!n) continue
    if (n.type === 'TEXT' && n.derivedTextGlyphs?.length) n.derivedTextGlyphs = []
    stack.push(...(n.childIds || []))
  }
}
await mkdir(outDir, { recursive: true })
const re = filter ? new RegExp(filter) : null
let n = 0
for (const page of graph.getPages()) {
  for (const id of page.childIds) {
    const node = graph.getNode(id)
    if (!node || node.visible === false) continue
    if (!node.name.startsWith('export/')) continue
    if (re && !re.test(node.name)) continue
    const png = await raster.headlessRenderNodes(graph, page.id, [id], { scale, format: 'PNG' })
    if (!png) { console.log('no image', node.name); continue }
    const name = node.name.replace(/^export\//, '').replace(/[^a-zA-Z0-9._-]+/g, '-')
    await writeFile(`${outDir}/${name}.png`, png)
    n++
    console.log('wrote', `${outDir}/${name}.png`, png.length)
  }
}
console.log('done', n)
process.exit(0)
