// 用 dsh 0.1.7-rc.2 自己的会话持久化插件(@deepseek-ai/dsh-session-persistence-jsonl)
// 写一组**合成**会话日志,给 session_facts.mjs 的测试当夹具。
//
// 用法(只在重新生成夹具时跑;测试本身不需要 dsh):
//   node make_fixture.mjs <装有 @deepseek-ai/dsh@0.1.7-rc.2 的 node_modules 目录> [输出目录]
// 输出目录缺省为本目录下的 home/sessions(即 DSH_HOME=本目录/home)。
//
// ⚠️ 事件全是编的:路径都在虚构的 C:\yz-fixture\ 与 C:\yz-outside\ 下,
// 不含任何真实用户的会话内容。日志的**字节格式**(目录布局、文件名、帧、
// header、事件行)由 dsh 自己的写入器产生 —— 这正是夹具要钉住的东西。
// ⚠️ 没做到的:事件**序列**只有 session_facts 读的那几种(step/start、tool/call、
// turn/*、step/end、session/end-seed),没有 assistant/message 流与 tool/result。
// dsh 自己的读取器会按生命周期校验拒绝这些日志(「tool/call has no advertised
// tool lifecycle」之类)—— 它们证明的是字节格式,不是一次真实运行。
//
// 夹具里有什么(测试按这张表断言):
//   yz-main-01    主会话,cwd = 项目。2 个 step;读项目内、读/写项目外、bash、pwsh、
//                 read_image、grep、str_replace_editor(view 与 create)
//   yz-kid:02     子代理(父 = yz-main-01);id 带冒号 → 目录名 yz-kid~003A02;1 个 step、bash
//   yz-fork-03    带继承前缀的子代理(isSeeded):前 4 条是主会话事件的副本
//                 (含 1 个 step 与一次读 notes.md),之后 1 个自己的 step
//   yz-grand-04   孙代理,parentSession = "yz-kid:02"(逻辑 id,与目录名不同);1 个 step、pwsh
//   yz-else-05    另一个项目(projects/other)的顶层会话,不该被选中、不该被并入

import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const NM = process.argv[2];
if (!NM) {
  console.log('用法: node make_fixture.mjs <node_modules 目录> [输出目录]');
  process.exit(2);
}
const here = path.dirname(fileURLToPath(import.meta.url));
const root = process.argv[3] ?? path.join(here, 'home', 'sessions');
const imp = (p) => import(pathToFileURL(path.join(NM, p)).href);
const { Context } = await imp('@deepseek-ai/cordis/lib/index.js');
const Jsonl = (await imp('@deepseek-ai/dsh-session-persistence-jsonl/lib/index.js')).default;

const ctx = new Context();
ctx.plugin(Jsonl, { root, compression: 'zstd' });
await new Promise(r => setTimeout(r, 200));
const store = ctx.get('sessionPersistence');

const W = (...parts) => ['C:', ...parts].join(String.fromCharCode(92));
const PROJECT = W('yz-fixture', 'projects', 'demo');
const OTHER = W('yz-fixture', 'projects', 'other');
const T0 = 1790000000000;

function log() {
  let seq = 0;
  const events = [];
  const add = (type, data) => { events.push({ type, seq, time: T0 + seq, data }); seq += 1; };
  const call = (turn, step, name, args) => add('tool/call', {
    turn, step, callId: 'c' + seq, name, arguments: JSON.stringify(args),
  });
  return { events, add, call };
}

async function write(id, meta, events, inheritedEventCount) {
  const header = { version: 4, id, createdAt: T0, isSeeded: false, delegationDepth: 0, ...meta };
  const handle = await store.create(header, inheritedEventCount === undefined ? {} : { inheritedEventCount });
  await handle.append(events);
  await handle.close?.();
}

// ── 主会话
const main = log();
main.add('turn/start', { turn: 1 });
main.add('step/start', { turn: 1, step: 1 });
main.call(1, 1, 'read', { file_path: path.join(PROJECT, 'task_plan.md') });
main.call(1, 1, 'read', { file_path: W('yz-outside', 'notes.md') });
main.call(1, 1, 'bash', { command: 'ls' });
main.call(1, 1, 'pwsh', { command: 'Get-ChildItem' });
main.call(1, 1, 'read_image', { file_path: W('yz-outside', 'chart.png') });
main.call(1, 1, 'write', { file_path: W('yz-fixture', 'scripts', 'checker.py'), content: 'x' });
main.add('step/end', { turn: 1, step: 1 });
main.add('step/start', { turn: 1, step: 2 });
main.call(1, 2, 'grep', { pattern: 'R1', path: W('yz-fixture', 'library', 'rules') });
main.call(1, 2, 'str_replace_editor', { command: 'view', path: path.join(OTHER, 'cards', 'L01.md') });
main.call(1, 2, 'str_replace_editor', { command: 'create', path: W('yz-outside', 'draft.md'), file_text: 'x' });
main.add('step/end', { turn: 1, step: 2 });
main.add('turn/end', { turn: 1, reason: 'completed' });
await write('yz-main-01', { cwd: PROJECT }, main.events);

// ── 子代理(id 带冒号,目录名会被转义)
const kid = log();
kid.add('step/start', { turn: 1, step: 1 });
kid.call(1, 1, 'read', { file_path: W('yz-outside', 'kid.md') });
kid.call(1, 1, 'bash', { command: 'cat x' });
kid.add('step/end', { turn: 1, step: 1 });
await write('yz-kid:02', { cwd: PROJECT, parentSession: 'yz-main-01', origin: 'subagent', delegationDepth: 1 }, kid.events);

// ── 带继承前缀的子代理:前 4 条是主会话事件的副本,然后是继承分界标记
const fork = log();
for (const e of main.events.slice(0, 4)) fork.add(e.type, e.data);
fork.add('session/end-seed', { inherited: true });
fork.add('step/start', { turn: 2, step: 1 });
fork.call(2, 1, 'read', { file_path: W('yz-outside', 'fork.md') });
fork.add('step/end', { turn: 2, step: 1 });
await write('yz-fork-03', { cwd: PROJECT, parentSession: 'yz-main-01', origin: 'subagent', delegationDepth: 1, isSeeded: true },
  fork.events, 4);

// ── 孙代理:parentSession 写的是子代理的逻辑 id
const grand = log();
grand.add('step/start', { turn: 1, step: 1 });
grand.call(1, 1, 'read', { file_path: W('yz-outside', 'grand.md') });
grand.call(1, 1, 'pwsh', { command: 'Get-Content y' });
grand.add('step/end', { turn: 1, step: 1 });
await write('yz-grand-04', { cwd: PROJECT, parentSession: 'yz-kid:02', origin: 'subagent', delegationDepth: 2 }, grand.events);

// ── 另一个项目的顶层会话
const other = log();
other.add('step/start', { turn: 1, step: 1 });
other.call(1, 1, 'read', { file_path: path.join(OTHER, 'task_plan.md') });
other.add('step/end', { turn: 1, step: 1 });
await write('yz-else-05', { cwd: OTHER }, other.events);

await ctx.stop?.();
console.log('夹具已写到 %s', root);
