#!/usr/bin/env node
// macOS-only optional launcher. Does not use or change GUI Krita's preferences.
import fs from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { BridgeClient, readConnection, defaultConnectionPath } from '../server/bridge.mjs';

if (process.platform !== 'darwin') throw new Error('This worker launcher supports macOS only. Use bootstrap.py inside Krita on other supported systems.');
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const executable = process.env.KRITA_RUNNER_PATH || '/Applications/krita.app/Contents/MacOS/kritarunner';
const connectionPath = process.env.KRITA_MCP_CONNECTION_FILE || defaultConnectionPath;
if (connectionPath !== defaultConnectionPath) throw new Error('This launcher uses the default bridge connection path. A custom backend needs its own launcher.');
const alive = pid => {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
};
try {
  const connection = await readConnection(connectionPath);
  if (alive(connection.pid)) {
    const state = await new BridgeClient({ connectionPath, timeoutMs: 5000 }).call('state', {});
    if (state.status !== 'ready') throw new Error('Existing bridge is not ready; inspect it before starting another worker.');
    process.stdout.write(JSON.stringify({ ready: true, alreadyRunning: true, pid: connection.pid }) + '\n');
    process.exit(0);
  }
} catch (error) { if (error.code !== 'BRIDGE_NOT_ACTIVE') throw error; }
await fs.access(executable, fs.constants.X_OK);
const profile = path.join(os.homedir(), '.local/share/krita-mcp-bridge/runner-profile');
const resources = path.join(profile, 'resources');
const preferences = path.join(profile, 'Library/Preferences');
await fs.mkdir(preferences, { recursive: true, mode: 0o700 });
await fs.mkdir(path.join(profile, 'Library/Application Support/kritarunner/swap'), { recursive: true, mode: 0o700 });
await fs.mkdir(path.join(profile, 'tmp'), { recursive: true, mode: 0o700 });
await fs.mkdir(path.join(resources, 'pykrita'), { recursive: true, mode: 0o700 });
const config = path.join(preferences, 'kritarunnerrc');
try { await fs.writeFile(config, `ResourceDirectory=${resources}\n`, { flag: 'wx', mode: 0o600 }); }
catch (error) {
  if (error.code !== 'EEXIST') throw error;
  if (!(await fs.readFile(config, 'utf8')).split(/\r?\n/).includes(`ResourceDirectory=${resources}`)) throw new Error('Existing dedicated runner configuration differs; inspect it before starting.');
}
const source = path.join(root, 'krita_plugin/krita_mcp_bridge');
const destination = path.join(resources, 'pykrita/krita_mcp_bridge');
let installed = false;
try { await fs.access(destination); installed = true; }
catch (error) { if (error.code !== 'ENOENT') throw error; }
if (installed) {
  for (const file of (await fs.readdir(source)).filter(name => name.endsWith('.py'))) {
    if (!(await fs.readFile(path.join(source, file))).equals(await fs.readFile(path.join(destination, file)))) throw new Error(`Installed Python differs: ${file}. Stop your separate worker and update its dedicated package manually; do not restart GUI Krita.`);
  }
} else await fs.cp(source, destination, { recursive: true, errorOnExist: true, force: false, filter: name => !name.includes('__pycache__') });
const logs = path.join(profile, 'logs', new Date().toISOString().replace(/[:.]/g, '-'));
await fs.mkdir(logs, { recursive: true, mode: 0o700 });
const stdout = await fs.open(path.join(logs, 'stdout.log'), 'wx', 0o600);
const stderr = await fs.open(path.join(logs, 'stderr.log'), 'wx', 0o600);
const child = spawn(executable, ['-s', 'krita_mcp_bridge.worker', '-f', 'run'], {
  cwd: root, detached: true, stdio: ['ignore', stdout.fd, stderr.fd],
  env: { ...process.env, CFFIXED_USER_HOME: profile, TMPDIR: path.join(profile, 'tmp'), QT_QPA_PLATFORM: 'cocoa', PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1' },
});
let spawnError;
child.once('error', error => { spawnError = error; });
child.unref();
await stdout.close(); await stderr.close();
for (let attempt = 0; attempt < 150; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 200));
  if (spawnError) throw spawnError;
  if (!alive(child.pid)) break;
  try {
    const connection = await readConnection(connectionPath);
    if (connection.pid !== child.pid) continue;
    const state = await new BridgeClient({ connectionPath, timeoutMs: 2000 }).call('state', {});
    if (state.status === 'ready') {
      process.stdout.write(JSON.stringify({ ready: true, pid: child.pid, logs }) + '\n');
      process.exit(0);
    }
  } catch { /* Wait for the real health response, not only the token file. */ }
}
process.stderr.write(`Worker readiness was not confirmed. Inspect ${logs}; do not blindly start another process.\n`);
process.exitCode = 1;
