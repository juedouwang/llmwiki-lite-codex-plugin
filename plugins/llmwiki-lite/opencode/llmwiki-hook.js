/* LLM Wiki Lite opencode hook: record file edits as fail-open dirty-path hints.
 * Loaded by opencode through the `plugin` config entry written by opencode/install.py.
 * The plugin stays dependency-free and calls the shared Python recorder with the
 * same PostToolUse JSON payload Codex and Claude Code send to hooks/hooks.json. */
import {spawn} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import path from 'node:path'

const EDIT_TOOLS = new Set(['write', 'edit', 'patch', 'multiedit', 'notebookedit', 'write_file', 'apply_patch'])

function recorder() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  return path.join(root, 'scripts', 'record_change.py')
}

function editedPaths(args) {
  const out = []
  for (const key of ['filePath', 'file_path', 'path']) {
    const value = args?.[key]
    if (typeof value === 'string' && value.trim()) out.push(value)
  }
  for (const key of ['paths', 'files']) {
    const value = args?.[key]
    if (!Array.isArray(value)) continue
    for (const item of value) if (typeof item === 'string' && item.trim()) out.push(item)
  }
  return out.slice(0, 100)
}

export const LlmWikiHook = async ({directory}, options) => ({
  'tool.execute.after': async (input) => {
    try {
      const tool = String(input?.tool ?? '')
      if (!EDIT_TOOLS.has(tool.toLowerCase())) return
      const paths = editedPaths(input?.args)
      if (paths.length === 0) return
      const payload = {
        hook_event_name: 'PostToolUse',
        tool_name: tool,
        cwd: directory,
        tool_input: {path: paths[0], paths},
        tool_response: {ok: true},
      }
      const python = options?.python || process.env.LLMWIKI_PYTHON || 'python'
      const child = spawn(python, ['-I', '-B', recorder()], {
        stdio: ['pipe', 'ignore', 'ignore'],
        windowsHide: true,
      })
      child.on('error', () => {})
      child.stdin.on('error', () => {})
      child.stdin.end(JSON.stringify(payload))
    } catch {
      /* Hooks must never break the host tool call. */
    }
  },
})
