// Minimal MCP stdio client for openpencil-mcp. Usage (as module):
//   import { connect } from './mcp.mjs'; const c = await connect(); await c.call('tool', {...})
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'

export async function connect({ root = process.cwd(), env = {} } = {}) {
  const proc = spawn('openpencil-mcp', [], {
    env: { ...process.env, OPENPENCIL_MCP_ROOT: root, ...env },
    stdio: ['pipe', 'pipe', 'pipe']
  })
  proc.stderr.on('data', (d) => process.stderr.write(`[mcp] ${d}`))
  const rl = createInterface({ input: proc.stdout })
  let nextId = 1
  const pending = new Map()
  rl.on('line', (line) => {
    let msg
    try { msg = JSON.parse(line) } catch { return }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      if (msg.error) reject(new Error(JSON.stringify(msg.error)))
      else resolve(msg.result)
    }
  })
  function request(method, params) {
    const id = nextId++
    proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (pending.has(id)) { pending.delete(id); reject(new Error(`timeout ${method}`)) }
      }, 120000)
    })
  }
  function notify(method, params) {
    proc.stdin.write(JSON.stringify({ jsonrpc: '2.0', method, params }) + '\n')
  }
  await request('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'skriv-design', version: '0.1' }
  })
  notify('notifications/initialized', {})
  return {
    proc,
    listTools: () => request('tools/list', {}),
    async call(name, args = {}) {
      const res = await request('tools/call', { name, arguments: args })
      if (res.isError) throw new Error(`${name}: ${JSON.stringify(res.content).slice(0, 2000)}`)
      return res
    },
    text(res) {
      return (res.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n')
    },
    close() { proc.stdin.end(); proc.kill() }
  }
}
