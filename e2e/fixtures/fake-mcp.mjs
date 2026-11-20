// A tiny MCP server over stdio (newline-delimited JSON-RPC) for the e2e specs. No network, no packages.
// Tools: search_notes (read-only hint), create_note, delete_note.
import readline from 'node:readline'

const TOOLS = [
  { name: 'search_notes', description: 'Find notes by a word.', inputSchema: { type: 'object', properties: { q: { type: 'string' } }, required: ['q'] }, annotations: { readOnlyHint: true } },
  { name: 'create_note', description: 'Write a new note.', inputSchema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } },
  { name: 'delete_note', description: 'Delete a note for good.', inputSchema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] }, annotations: { destructiveHint: true } },
]
const send = (m) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...m }) + '\n')
readline.createInterface({ input: process.stdin }).on('line', (line) => {
  let m
  try {
    m = JSON.parse(line)
  } catch {
    return
  }
  if (m.id === undefined) return // notifications
  if (m.method === 'initialize')
    send({ id: m.id, result: { protocolVersion: m.params?.protocolVersion ?? '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fake-notes', version: '1.0.0' } } })
  else if (m.method === 'tools/list') send({ id: m.id, result: { tools: TOOLS } })
  else if (m.method === 'ping') send({ id: m.id, result: {} })
  else send({ id: m.id, error: { code: -32601, message: 'not here' } })
})
