// session_facts — 从 DSH 会话日志取本轮的两个事实:步数、项目外读取路径。
//
// 用法:
//   node session_facts.mjs --project <项目目录> [--toolkit <工具包根>]
//                          [--session <会话id片段>] [--json 输出.json]
//
// 退出码:0 取到 · 2 用法错 · 3 未取到会话日志(含「注入的会话变量无效」)·
//         4 结果算出来了、也打印了,但 --json 没写成(那份 json 不能用)
//
// ── 会话日志在哪、长什么样(v1.1.0 按 dsh 0.1.7-rc.2 适配)
//   根目录 = `$DSH_HOME/sessions`;DSH_HOME 未设或为空白时是 `~/.dsh/sessions`
//   (与 dsh 自己的 resolveDshHome 同一规则)。其下两层目录:
//     <项目目录键>/<会话目录>/<日志文件>
//   0.1.7-rc.2 的项目目录键形如 `--C-work-projects-demo--`,会话目录 = 会话 id
//   逐字符转义(不安全字符写成 `~XXXX`);日志文件名 `session.v<N>.jsonl.zstd`
//   (N = 格式代数,当前 4;不压缩时没有 `.zstd`),旧版是 `session.jsonl.zstd`。
//   一个会话目录里若有几代日志,取代数最高的那份 —— 低代是迁移前的只读旧件,
//   两份都读会把步数算两遍。
//   日志 = 若干个独立 zstd 帧首尾相接;首行是 header
//   `{type:"session", version, id, createdAt, cwd?, parentSession?, origin?, isSeeded, delegationDepth}`,
//   其后每行一个事件 `{type, seq, time, data}`。本脚本读 `step/start` 与
//   `tool/call`(`data.name` + `data.arguments`,后者是 JSON 字符串)。
//   ⚠️ 帧按帧头结构切(与 dsh 自己的 scanZstdFrames 同一套规则),不再只找魔数:
//   魔数的四个字节也可能出现在压缩数据中间,按魔数切会把一帧切成两半、静默丢事件。
//   末尾半帧(写到一半)尽量解出前缀;结构坏掉时退回按魔数找帧。凡有丢弃都记进
//   `log_notes`,不静默。
//
// ── 清单要分组,不能平铺
//   一次实测里归并子代理后是 66 条,而真正越界的那 4–5 条项目目录外路径埋在第
//   4/12/13/14 位,前后全是 library/rules、templates、scripts、别的项目的 cards。
//   按本文件自己写的那条「满是噪声的清单等于没有这一段」,窗口卡上这么列等于没列。
//   所以每条带 `scope` 四分:
//     outside_toolkit  工具包根之外 —— **真越界**,控制台逐条全列
//     harness_temp     `dsh-spill-*` 下的溢写文件 —— harness 把工具输出写到盘上,
//                      子代理再读回来,读的是**它自己的工具输出**,不是越界
//     other_project    工具包内、但属于别的项目
//     toolkit_internal 工具包内的 library / templates / scripts / 规格
//   取不到工具包根时是第五个值 `unclassified`(不分组,逐条全列)。
//   后三组的**纯读取**折叠成一行计数;⛔ 任一项目外写入不论 scope 都逐条列,
//   JSON 里各组一条不少 —— 折叠的是显示,不是事实。
//   工具包根:`--toolkit` 显式给;缺省时,仅当 `--project` 的父目录名为
//   `projects` 才取其上一级。取不到就**不猜**,退回不分组并说明。
//
// ⚠️ 为什么是 node 不是 python:会话日志是 multi-frame zstd,而工具包跑的
// Python 3.12 标准库没有 zstd。
//
// ── 会话选法(按优先级;选中哪一份、凭什么选的,一定打印出来)
//   1. DSH_SESSION_JSONL —— 旧版 harness 注入的本会话日志绝对路径(0.1.7 已不注入)
//   2. DSH_SESSION_ID    —— harness 给每次 shell 工具调用注入的会话 id
//   3. --session <id 片段>
//   4. 启发式:最新一份「提到过这个项目」的**顶层**日志(header 的 cwd 在项目内,
//      或有工具调用的绝对路径落在项目内;`origin: "subagent"` 的不算)
//   ⛔ 第 4 档在五项目实测里选错 3/5(有的项目拿到了另一个项目的 255 步,有的
//      拿到了第三个项目的 102)—— 所以它排在最后,而且**选法必须随结果一起报**,
//      否则一个属于别的任务的数字看起来和对的一模一样。
//   ⛔ v1.1.0 · C-5:1 或 2 **设了但无效**(文件不在 / 会话根下找不到这个 id)时
//      **不回落**到 3、4:那等于让启发式冒充 harness 的选择,协议违规静默通过。
//      打印原因、exit 3、json 里 `session_pick_error` 写明。
//
// ── 子代理
//   按 `parentSession` **递归**归并(每份日志首行的 header 带 `parentSession` 与
//   `origin: "subagent"`)。⛔ 不归并的话,实测那 10 份子代理日志里读的三个
//   项目目录外的文件一条都不会出现在清单里 —— 而它们同样是项目外读取。
//   子代理日志开头若是从父会话继承来的副本(`session/end-seed` 且
//   `data.inherited: true` 之前的事件),那一段不再计入 —— 父会话已经算过一遍。
//
// ── 已知盲区(写在这里而不是留给读的人猜)
//   shell 工具(`pwsh` 与 `bash`,v1.1.0 起两者一样计数)的参数是一整条命令行
//   —— 命令里 cat/Get-Content 读了什么,这里看不见。相对路径的读取同理。所以
//   「项目外读取」是**下限不是全集**,报告里必须照这个口径写。
//   ⛔ 取不到会话时本脚本 **exit 3**,并且不打印「0 条」——「没找到」被读成
//   「没越界」比不写更糟。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';

const SLASH = '/';
const BACKSLASH = String.fromCharCode(92);  // 不写字面反斜杠:它在通往这里的链上被吃掉过
const LF = String.fromCharCode(10);
const ZSTD_MAGIC = 0xfd2fb528;               // 小端读出的帧魔数(字节序 28 B5 2F FD)
const ZSTD_MAGIC_BYTES = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

/** shell 工具:命令行不解析,只计次数。 */
const SHELL_TOOLS = ['pwsh', 'bash'];

/** 与 dsh 的 resolveDshHome 同一规则:DSH_HOME(非空白)> ~/.dsh。 */
function dshHome() {
  const raw = process.env.DSH_HOME;
  if (raw !== undefined && raw.trim() !== '') return path.resolve(expandHome(raw));
  return path.join(os.homedir(), '.dsh');
}

function expandHome(p) {
  if (p === '~') return os.homedir();
  if (p.startsWith('~' + SLASH) || p.startsWith('~' + BACKSLASH)) return path.join(os.homedir(), p.slice(2));
  return p;
}

const SESS_ROOT = path.join(dshHome(), 'sessions');

/** 解帧时丢掉了什么 —— 本轮用到的日志写进 json 的 log_notes,不静默。 */
const LOG_NOTES = [];
function note(notes, file, text) {
  if (notes !== null) notes.push(path.basename(path.dirname(file)) + ': ' + text);
}

/**
 * 按帧头结构切 zstd 帧(移植自 dsh 0.1.7 的 scanZstdFrames)。
 * @returns {{frames: {start:number,end:number}[], tornStart?: number, corruptAt?: number}}
 */
function scanFrames(buf) {
  const frames = [];
  let offset = 0;
  while (offset < buf.length) {
    const start = offset;
    if (buf.length - offset < 4) return { frames, tornStart: start };
    if (buf.readUInt32LE(offset) !== ZSTD_MAGIC) return { frames, corruptAt: offset };
    offset += 4;
    if (offset === buf.length) return { frames, tornStart: start };
    const descriptor = buf.readUInt8(offset);
    offset += 1;
    if ((descriptor & 24) !== 0) return { frames, corruptAt: start };
    const contentSizeFlag = descriptor >>> 6;
    const singleSegment = (descriptor & 32) !== 0;
    const checksum = (descriptor & 4) !== 0;
    const dictionaryFlag = descriptor & 3;
    const dictionaryBytes = dictionaryFlag === 3 ? 4 : dictionaryFlag;
    const contentSizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 1 << contentSizeFlag;
    const headerRest = (singleSegment ? 0 : 1) + dictionaryBytes + contentSizeBytes;
    if (buf.length - offset < headerRest) return { frames, tornStart: start };
    offset += headerRest;
    for (;;) {
      if (buf.length - offset < 3) return { frames, tornStart: start };
      const blockHeader = buf.readUIntLE(offset, 3);
      offset += 3;
      const lastBlock = (blockHeader & 1) !== 0;
      const blockType = (blockHeader >>> 1) & 3;
      const blockSize = blockHeader >>> 3;
      if (blockType === 3) return { frames, corruptAt: start };
      const payload = blockType === 1 ? 1 : blockSize;
      if (buf.length - offset < payload) return { frames, tornStart: start };
      offset += payload;
      if (lastBlock) break;
    }
    if (checksum) {
      if (buf.length - offset < 4) return { frames, tornStart: start };
      offset += 4;
    }
    frames.push({ start, end: offset });
  }
  return { frames };
}

function inflate(buf, file, what, notes) {
  try {
    return zlib.zstdDecompressSync(buf);
  } catch (err) {
    note(notes, file, what + '解不开(' + String(err && err.message) + '),已丢弃');
    return Buffer.alloc(0);
  }
}

/** 一份日志的明文。⚠️ 也接受未压缩的 jsonl(不压缩的 dsh 配置、旧版注入的明文路径)。 */
function logText(file, notes = null) {
  const buf = fs.readFileSync(file);
  if (buf.length < 4 || buf.compare(ZSTD_MAGIC_BYTES, 0, 4, 0, 4) !== 0) return buf.toString('utf8');
  const scan = scanFrames(buf);
  // ⚠️ 先把各帧的字节接起来再按 UTF-8 解:一个多字节字符可能跨在两帧之间。
  const parts = [];
  for (const f of scan.frames) parts.push(inflate(buf.subarray(f.start, f.end), file, '第 ' + f.start + ' 字节起的一帧', notes));
  if (scan.tornStart !== undefined) {
    // 写到一半的末帧:尽量解出已写入的前缀(最后半行由 JSON.parse 丢掉)。
    try {
      parts.push(zlib.zstdDecompressSync(buf.subarray(scan.tornStart),
        { finishFlush: zlib.constants.ZSTD_e_flush }));
      note(notes, file, '末尾有写到一半的帧(第 ' + scan.tornStart + ' 字节起),只取到它已写入的前缀');
    } catch {
      note(notes, file, '末尾有写到一半的帧(第 ' + scan.tornStart + ' 字节起),解不出,已丢弃');
    }
  }
  if (scan.corruptAt !== undefined) {
    // 结构坏了:退回老办法,按魔数找下一帧,能解几帧解几帧。
    note(notes, file, '第 ' + scan.corruptAt + ' 字节起帧结构不合法,改按魔数找帧(可能有丢失)');
    const starts = [];
    for (let i = scan.corruptAt + 1; i + 4 <= buf.length; i++) {
      if (buf.compare(ZSTD_MAGIC_BYTES, 0, 4, i, i + 4) === 0) starts.push(i);
    }
    for (let k = 0; k < starts.length; k++) {
      const end = k + 1 < starts.length ? starts[k + 1] : buf.length;
      try { parts.push(zlib.zstdDecompressSync(buf.subarray(starts[k], end))); } catch { /* 记在上面那条里 */ }
    }
  }
  return Buffer.concat(parts).toString('utf8');
}

/** 解一份日志 → 事件数组(首行 header 也在内)。解析不了的行计数入 log_notes。 */
function decode(file, notes = null) {
  const events = [];
  let broken = 0;
  const lines = logText(file, notes).split(LF);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim()) continue;
    try { events.push(JSON.parse(line)); } catch { broken += 1; }
  }
  if (broken > 0) note(notes, file, broken + ' 行不是合法 JSON,已丢弃');
  return events;
}

/** header 只解第一帧(大日志不必整份解压)。 */
function readHeader(file) {
  let text;
  try {
    const buf = fs.readFileSync(file);
    if (buf.length >= 4 && buf.compare(ZSTD_MAGIC_BYTES, 0, 4, 0, 4) === 0) {
      const scan = scanFrames(buf);
      const first = scan.frames[0];
      text = first === undefined ? logText(file) : zlib.zstdDecompressSync(buf.subarray(first.start, first.end)).toString('utf8');
    } else {
      text = buf.toString('utf8');
    }
  } catch {
    return {};
  }
  const line = text.split(LF).find(l => l.trim() !== '');
  if (line === undefined) return {};
  try {
    const v = JSON.parse(line);
    return (v !== null && typeof v === 'object' && !Array.isArray(v)) ? v : {};
  } catch {
    return {};
  }
}

/** 会话目录里的日志文件名:`session.jsonl` 或 `session.v<N>.jsonl`,可带 `.zstd`。 */
const LOG_NAME = /^session(?:\.v([1-9][0-9]*))?\.jsonl(\.zstd)?$/;

/** 一个会话目录里当前那一代日志;没有返回 null。 */
function currentLog(dir) {
  let best = null;
  let names;
  try { names = fs.readdirSync(dir); } catch { return null; }
  for (const name of names) {
    const m = LOG_NAME.exec(name);
    if (m === null) continue;
    const gen = m[1] === undefined ? 0 : Number(m[1]);
    const zstd = m[2] !== undefined;
    if (best === null || gen > best.gen || (gen === best.gen && zstd && !best.zstd)) {
      best = { file: path.join(dir, name), gen, zstd };
    }
  }
  return best;
}

function isDir(p) {
  try { return fs.statSync(p).isDirectory(); } catch { return false; }
}

/** 全部会话日志(新到旧)。header 懒解析。 */
function sessionIndex() {
  if (!isDir(SESS_ROOT)) return [];
  const found = [];
  for (const group of fs.readdirSync(SESS_ROOT)) {
    const groupDir = path.join(SESS_ROOT, group);
    if (!isDir(groupDir)) continue;
    for (const sid of fs.readdirSync(groupDir)) {
      const dir = path.join(groupDir, sid);
      if (!isDir(dir)) continue;
      const log = currentLog(dir);
      if (log === null) continue;
      found.push({ file: log.file, sid, generation: log.gen, mtime: fs.statSync(log.file).mtimeMs, header: null });
    }
  }
  return found.sort((a, b) => b.mtime - a.mtime);
}

function headerOf(entry) {
  if (entry.header === null) entry.header = readHeader(entry.file);
  return entry.header;
}

/** 会话 id → 会话目录名(移植 dsh 0.1.7 的 encodeSegment:不安全字符写成 `~XXXX`)。 */
function encodeSegment(raw) {
  if (raw === '.') return '~002E';
  if (raw === '..') return '~002E~002E';
  let out = '';
  for (let i = 0; i < raw.length; i++) {
    const code = raw.charCodeAt(i);
    const ch = String.fromCharCode(code);
    if (ch !== '~' && /^[A-Za-z0-9._-]$/.test(ch)) out += ch;
    else out += '~' + code.toString(16).toUpperCase().padStart(4, '0');
  }
  return out;
}

/**
 * 一次工具调用里出现的**绝对**路径,以及这是读还是写。
 *
 * ⛔ 只收绝对路径,而且按工具分别取键:
 *  - read/read_image/edit/write 的 `file_path`、str_replace_editor 的 `path` 是真路径
 *  - grep 的 `pattern` 是**正则**不是路径 —— 收它会把「单品|产能竞争」报成
 *    越界读取,而一段满是噪声的清单等于没有这一段
 *  - glob 的 `pattern` 与 grep 的 `path` 多为相对,而相对于哪个 cwd 我们不知道;
 *    拿进程 cwd 去 resolve 会造出根本没被读过的路径
 * ⚠️ 代价写明:相对路径的读取因此不在清单里,与 shell 同属盲区,报告要照实说。
 */
const PATH_KEYS = {
  read: ['file_path'], read_image: ['file_path'], edit: ['file_path'], write: ['file_path'],
  grep: ['path'], glob: ['path'], str_replace_editor: ['path'],
};

/** 工具动作:write/edit 是**写**不是「读过」—— 标签错了会把违规说成常规。 */
const TOOL_ACTION = { read: '读', read_image: '读', grep: '读', glob: '读', write: '写', edit: '写' };

function callInfo(call) {
  const tool = String(call?.name ?? '');
  // hasOwn:工具名来自日志,`constructor` 之类的名字不能摸到原型链上去。
  const keys = Object.hasOwn(PATH_KEYS, tool) ? PATH_KEYS[tool] : undefined;
  if (keys === undefined) return { tool, paths: [], action: '?' };
  let args;
  try { args = JSON.parse(String(call.arguments ?? '{}')); } catch { return { tool, paths: [], action: '?' }; }
  if (args === null || typeof args !== 'object') return { tool, paths: [], action: '?' };
  const paths = [];
  for (const key of keys) {
    const v = args[key];
    if (typeof v === 'string' && path.isAbsolute(v)) paths.push(v);
  }
  const action = tool === 'str_replace_editor'
    ? (args.command === 'view' ? '读' : '写')
    : (Object.hasOwn(TOOL_ACTION, tool) ? TOOL_ACTION[tool] : '?');
  return { tool, paths, action };
}

/** 归一化:正斜杠、绝对化。⚠️ 只为比较,报告里给原样路径。 */
function norm(p) {
  return path.resolve(p).split(BACKSLASH).join(SLASH);
}

/** 比较用键:Windows 路径大小写不敏感 —— 只小写盘符会让 --project 大小写不合就「未找到」。 */
function cmpKey(p) {
  const n = norm(p);
  return process.platform === 'win32' ? n.toLowerCase() : n;
}

/**
 * 在项目目录之内?
 * ⛔ 必须带分隔符:裸 startsWith 会把 `…/研究-B2` 当成 `…/研究-B` 的内部,
 * 于是同前缀的兄弟目录被算作项目内,越界读取凭空少几条。
 */
function insideProject(absKey, projectKey) {
  return absKey === projectKey || absKey.startsWith(projectKey + SLASH);
}

/**
 * 工具包根。⛔ 只在父目录名恰为 `projects` 时才推断,否则返回 null ——
 * 猜错了会把「真越界」判成「工具包内部」,而那正是这一段要抓的东西。
 */
function toolkitRootOf(projectPath, explicit) {
  if (explicit !== null && explicit !== undefined && explicit !== '') return cmpKey(explicit);
  const parent = path.dirname(path.resolve(projectPath));
  return path.basename(parent).toLowerCase() === 'projects' ? cmpKey(path.dirname(parent)) : null;
}

/**
 * harness 把工具输出溢写到盘上的目录 —— 子代理读回来的是**它自己的工具输出**,
 * 不是越界。混进 outside_toolkit 会把真越界稀释掉:实测 15 条里有 5 条是这个。
 * 判据只认 `dsh-spill-*` 这一层目录名。
 */
function isHarnessTemp(absKey) {
  return absKey.split(SLASH).some(seg => seg.startsWith('dsh-spill-'));
}

/**
 * 只是提示,**不改分组**。⛔ 判据故意保持窄:`dsh-` 是 harness 的命名约定,
 * agent 自己也能往那里写 —— 放宽成「凡 dsh- 临时目录都不算越界」等于给出一条
 * 洗白路径(把项目目录外的文件抄进 Temp/dsh-foo 再读回来,清单上就看不见了)。
 * 所以这类条目留在越界组,只加一行字告诉读卡的人它长什么样。
 * (中间路「按谁写的分」记在册,暂不做。)
 */
const TEMP_HINT = 'harness 临时目录(非 spill)—— 窄判据保留在越界组,人工判';

function hintFor(absKey, scope) {
  if (scope !== 'outside_toolkit') return null;
  return absKey.split(SLASH).some(seg => seg.startsWith('dsh-')) ? TEMP_HINT : null;
}

/** 一条项目外路径落在哪一组。toolkitKey 为 null 时不细分。 */
function scopeOf(absKey, toolkitKey) {
  if (isHarnessTemp(absKey)) return 'harness_temp';
  if (toolkitKey === null) return 'unclassified';
  if (!insideProject(absKey, toolkitKey)) return 'outside_toolkit';
  return insideProject(absKey, toolkitKey + SLASH + 'projects') ? 'other_project' : 'toolkit_internal';
}

/** 「提到过这个项目」:header 的 cwd 在项目内,或有工具调用的绝对路径落在项目内。 */
function mentionsProject(entry, projectKey) {
  const cwd = headerOf(entry).cwd;
  if (typeof cwd === 'string' && path.isAbsolute(cwd) && insideProject(cmpKey(cwd), projectKey)) return true;
  return decode(entry.file).some(e => e.type === 'tool/call'
    && callInfo(e.data).paths.some(p => insideProject(cmpKey(p), projectKey)));
}

function isSet(v) {
  return v !== undefined && v !== null && String(v).trim() !== '';
}

/**
 * 选会话。返回 {entry, how}、{error, variable} 或 null。
 * ⛔ 注入的变量设了却无效 → {error},**不回落**到 --session / 启发式(C-5)。
 */
function chooseSession(index, projectKey, wanted) {
  const byJsonl = process.env.DSH_SESSION_JSONL;
  if (isSet(byJsonl)) {
    let ok = false;
    try { ok = fs.statSync(byJsonl).isFile(); } catch { ok = false; }
    if (!ok) {
      return { variable: 'DSH_SESSION_JSONL',
        error: 'DSH_SESSION_JSONL 指向的文件不存在:' + byJsonl };
    }
    const sid = path.basename(path.dirname(byJsonl));
    return { entry: { file: byJsonl, sid, header: null }, how: 'DSH_SESSION_JSONL(harness 注入)' };
  }
  const byId = process.env.DSH_SESSION_ID;
  if (isSet(byId)) {
    const dirName = encodeSegment(byId);
    let hit = index.find(e => e.sid === byId || e.sid === dirName);
    if (hit === undefined) hit = index.find(e => String(headerOf(e).id ?? '') === byId);
    if (hit !== undefined) return { entry: hit, how: 'DSH_SESSION_ID(harness 注入)' };
    return { variable: 'DSH_SESSION_ID',
      error: 'DSH_SESSION_ID=' + byId + ' 在 ' + SESS_ROOT + ' 下找不到(共 ' + index.length + ' 份会话)' };
  }
  if (isSet(wanted)) {
    let hit = index.find(e => e.sid.includes(wanted));
    if (hit === undefined) hit = index.find(e => String(headerOf(e).id ?? '').includes(wanted));
    if (hit !== undefined) return { entry: hit, how: '--session ' + wanted };
  }
  for (const entry of index) {
    // 启发式只挑顶层会话:子代理的 header 也带着父会话的 cwd,最新的那份常常是
    // 子代理 —— 选中它,steps_used 就成了子代理的步数。
    if (headerOf(entry).origin === 'subagent') continue;
    if (mentionsProject(entry, projectKey)) {
      return { entry, how: '启发式:最新一份提到该项目的日志(⚠️ 五项目实测选错 3/5,仅在没有 harness 变量时用)' };
    }
  }
  return null;
}

/** 主会话的全部后代子代理(按 parentSession 递归归并,坏日志成环也不死循环)。 */
function subagentsOf(index, rootSid, rootDirSid = rootSid) {
  const seen = new Set([String(rootSid), String(rootDirSid)]);
  const descendants = [];
  let added = true;
  while (added) {
    added = false;
    for (const entry of index) {
      const header = headerOf(entry);
      const sid = String(header.id ?? entry.sid);
      const parent = String(header.parentSession ?? '');
      if (seen.has(parent) && !seen.has(sid)) {
        seen.add(sid);
        seen.add(String(entry.sid));
        descendants.push(entry);
        added = true;
      }
    }
  }
  return descendants;
}

/**
 * 子代理自己的事件:去掉从父会话继承来的前缀(最后一个
 * `session/end-seed` 且 `data.inherited === true` 及其之前的一切)。
 */
function ownEvents(events) {
  let cut = -1;
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e && e.type === 'session/end-seed' && e.data && e.data.inherited === true) cut = i;
  }
  if (cut === -1) return { events, inherited: 0 };
  // 数给人看的「继承了几条」不含首行 header。
  const inherited = events.slice(0, cut).filter(e => !(e && e.type === 'session')).length;
  return { events: events.slice(cut + 1), inherited };
}

const USAGE = '用法: node session_facts.mjs --project <项目目录> [--toolkit <工具包根>] '
  + '[--session <id片段>] [--json 输出.json]';

function main(argv) {
  if (argv.includes('--help') || argv.includes('-h')) {
    console.log(USAGE);
    return 0;
  }
  const valued = ['--project', '--toolkit', '--session', '--json'];
  for (const name of valued) {
    const i = argv.indexOf(name);
    if (i !== -1 && (i + 1 >= argv.length || argv[i + 1].startsWith('--'))) {
      console.log('%s 后面要跟一个值。', name);
      console.log(USAGE);
      return 2;
    }
  }
  const opt = (name) => { const i = argv.indexOf(name); return i === -1 ? null : argv[i + 1]; };
  const project = opt('--project');
  if (project === null) {
    console.log(USAGE);
    return 2;
  }
  const jsonPath = opt('--json');
  const projectKey = cmpKey(project);
  const toolkitKey = toolkitRootOf(project, opt('--toolkit'));
  const index = sessionIndex();
  const picked = chooseSession(index, projectKey, opt('--session'));

  if (picked !== null && picked.error !== undefined) {
    // ⛔ C-5:harness 说了「是这一份」,而这一份找不到 —— 不许换一份凑数。
    console.log('未取到会话日志 —— 注入的会话变量无效,不回落到 --session 或启发式。');
    console.log('  原因: %s', picked.error);
    console.log('  会话根: %s', SESS_ROOT);
    console.log('  ⛔ 步数与项目外读取都**无法取**;报告写「未取到会话日志」,不许写 0、不许留空清单。');
    console.log('  排查:脚本读的会话根与 harness 用的是否同一个(DSH_HOME);变量是否来自别的会话。');
    writeJson(jsonPath, {
      session: null, session_picked_by: null, steps_used: null,
      outside_reads: null, sessions_root: SESS_ROOT,
      session_pick_error: picked.error,
      note: '注入的会话变量无效(' + picked.variable + '):未取到会话日志,不要写 0',
    });
    return 3;
  }

  if (picked === null) {
    // ⛔ 不打「0 条」、不写 0:「没找到」和「没越界」长得一模一样,后果相反。
    console.log('未取到会话日志 —— 没有任何日志可归到这个项目。');
    console.log('  项目: %s', project);
    console.log('  找过: %s(%d 份会话)', SESS_ROOT, index.length);
    console.log('  ⛔ 步数与项目外读取都**无法取**;报告写「未取到会话日志」,不许写 0、不许留空清单。');
    writeJson(jsonPath, {
      session: null, session_picked_by: null, steps_used: null,
      outside_reads: null, sessions_root: SESS_ROOT,
      note: '未取到会话日志:步数与项目外读取都无法取,不要写 0',
    });
    return 3;
  }

  const entry = picked.entry;
  // ⛔ 选中了却一条事件都解不出来 = 没取到,不是「这一轮什么都没干」。
  // 原来这条路走下去打印「步数 0 · 项目外路径 0 条」并 exit 0 —— 正是本文件
  // 开头明令禁止的那种沉默(对抗核查 2026-09-04:把 DSH_SESSION_JSONL 指向
  // 一个非 json 文件即可复现)。
  const mainEvents = decode(entry.file, LOG_NOTES);
  if (mainEvents.length === 0) {
    console.log('未取到会话日志 —— 选中的日志解不出任何事件。');
    console.log('  选中: %s(选法 %s)', entry.sid, picked.how);
    console.log('  文件: %s', entry.file);
    console.log('  ⛔ 步数与项目外读取都**无法取**;报告写「未取到会话日志」,不许写 0、不许留空清单。');
    writeJson(jsonPath, {
      session: entry.sid, session_picked_by: picked.how, steps_used: null,
      outside_reads: null, sessions_root: SESS_ROOT,
      note: '选中的会话日志解不出事件:无法取数,不要写 0',
    });
    return 3;
  }
  const rootSid = String(headerOf(entry).id ?? entry.sid);
  const kids = subagentsOf(index, rootSid, entry.sid);
  const sources = [{ label: '主会话', sid: entry.sid, file: entry.file }]
    .concat(kids.map(k => ({ label: '子代理', sid: k.sid, file: k.file })));

  // ⚠️ 步数分两个数报,不合成一个:
  //   `steps_used` = **主会话**的 step/start 计数 —— 这是独立复核核过的 255,
  //   也是规格里「本会话」的读法,预算对账用它。
  //   `steps_incl_subagents` = 主会话 + 全部子代理(同一次实测 536)。
  // 把两者合成一个数会让一个已经核过的数字凭空翻倍,而看不出是口径变了还是
  // 跑错了会话。用哪个当预算基准由规格定,不在这里替它决定。
  let stepsMain = 0;
  let stepsAll = 0;
  const shellByTool = Object.fromEntries(SHELL_TOOLS.map(t => [t, 0]));
  const outside = new Map();
  for (const src of sources) {
    let events = src.label === '主会话' ? mainEvents : decode(src.file, LOG_NOTES);
    if (src.label === '子代理') {
      const own = ownEvents(events);
      if (own.inherited > 0) {
        note(LOG_NOTES, src.file, '开头 ' + own.inherited + ' 条事件是从父会话继承的副本,未重复计入');
      }
      events = own.events;
    }
    const n = events.filter(e => e.type === 'step/start').length;
    stepsAll += n;
    if (src.label === '主会话') stepsMain += n;
    for (const e of events) {
      if (e.type !== 'tool/call') continue;
      const info = callInfo(e.data);
      if (Object.hasOwn(shellByTool, info.tool)) shellByTool[info.tool] += 1;
      for (const raw of info.paths) {
        const key = cmpKey(raw);
        if (insideProject(key, projectKey)) continue;
        const action = info.action;
        const prior = outside.get(key);
        if (prior === undefined || (prior.action !== '写' && action === '写')) {
          const scope = prior?.scope ?? scopeOf(key, toolkitKey);
          outside.set(key, {
            path: prior?.path ?? raw, tool: info.tool, action,
            scope, hint: hintFor(key, scope),
            from: src.label, session: src.sid, turn: e.data?.turn ?? null,
          });
        }
      }
    }
  }

  const reads = [...outside.values()];
  const counts = { outside_toolkit: 0, harness_temp: 0, other_project: 0, toolkit_internal: 0, unclassified: 0 };
  for (const r of reads) counts[r.scope] += 1;
  const shellCalls = SHELL_TOOLS.reduce((sum, t) => sum + shellByTool[t], 0);

  const written = emit({
    session: entry.sid,
    session_picked_by: picked.how,
    captured_at: new Date().toISOString(),
    sessions_root: SESS_ROOT,
    subagent_sessions: kids.map(k => k.sid),
    steps_used: stepsMain,
    steps_incl_subagents: stepsAll,
    toolkit_root: toolkitKey,
    outside_reads: reads,
    scope_counts: counts,
    shell_calls: shellCalls,
    shell_calls_by_tool: shellByTool,
    log_notes: LOG_NOTES.slice(),
    note: 'outside_reads 是下限:shell(pwsh / bash)的命令行未解析,相对路径的读取也不在内',
  }, jsonPath);
  return written ? 0 : 4;
}

/**
 * 写 json。⛔ 目录不存在就先建 —— 原来直接 writeFileSync,ENOENT 一抛,
 * node 以 rc=1 退出,**而且已经算好的结果连一行都没打印出来**(异常发生在
 * 打印之前)。写失败也不许吞:说出来,但不要连带把结果吞掉。
 * ⛔ v1.1.0 · C-4:写失败要让退出码知道(调用方据此判断 json 不能用)。同一路径
 * 上若还留着上一轮的 json,试着删掉 —— 留着它,⑧ 会拿上一轮的数核这一轮。
 * @returns 写成了(或没要求写)→ true
 */
function writeJson(jsonPath, facts) {
  if (jsonPath === null || jsonPath === undefined) return true;
  try {
    fs.mkdirSync(path.dirname(path.resolve(jsonPath)), { recursive: true });
    fs.writeFileSync(jsonPath, JSON.stringify(facts, null, 1), 'utf8');
    return true;
  } catch (err) {
    console.log('⛔ 结果没能写进 %s:%s', jsonPath, String(err && err.message));
    let stale = false;
    try {
      if (fs.statSync(jsonPath).isFile()) {
        try { fs.rmSync(jsonPath, { force: true }); } catch { /* 下面再看它还在不在 */ }
        stale = fs.existsSync(jsonPath);
      }
    } catch { stale = false; }
    if (stale) {
      console.log('⛔ %s 仍是上一轮留下的旧文件,删不掉 —— 别拿它核这一轮。', jsonPath);
    }
    return false;
  }
}

function emit(facts, jsonPath) {
  console.log('会话 %s · 选法 %s', facts.session, facts.session_picked_by);
  console.log('  取数时刻 %s', facts.captured_at);
  if (facts.subagent_sessions.length > 0) {
    console.log('  并入子代理 %d 份: %s', facts.subagent_sessions.length,
      facts.subagent_sessions.map(s => s.slice(0, 8)).join(', '));
  }
  const stepsText = facts.steps_incl_subagents === facts.steps_used
    ? String(facts.steps_used)
    : facts.steps_used + '(主会话)/ ' + facts.steps_incl_subagents + '(含子代理)';
  const byTool = SHELL_TOOLS.filter(t => facts.shell_calls_by_tool[t] > 0)
    .map(t => t + ' ' + facts.shell_calls_by_tool[t]).join(' · ');
  console.log('步数 %s · 项目外路径 %d 条%s', stepsText, facts.outside_reads.length,
    facts.shell_calls > 0 ? ' · shell 调用 ' + facts.shell_calls + ' 次(' + byTool + ';命令行未解析)' : '');
  // ⛔ 真越界那一组一条不折叠:实测中原来只打前 12 条,被截掉的后半里有一条
  // `write scripts/liveness_probe.py`(公理 7 违规),控制台上根本看不到。
  // 后三组的纯读取折叠成一行 —— 66 条平铺时,真越界的 4 条埋在
  // library/templates 中间,等于没列。⛔ 任一项目外写入不论 scope 都逐条列;
  // 折叠的只是显示:JSON 里各组一条不少。
  const c = facts.scope_counts;
  if (facts.toolkit_root === null) {
    console.log('⚠️ 取不到工具包根(--project 的父目录不叫 projects,也没给 --toolkit)—— 本次不分组,逐条全列:');
  } else {
    console.log('真越界(工具包根之外)%d 条；其余 scope 的写入也逐条列:', c.outside_toolkit);
  }
  for (const r of facts.outside_reads) {
    if (facts.toolkit_root !== null && r.scope !== 'outside_toolkit' && r.action !== '写') continue;
    const tail = r.from === '子代理' ? '  (子代理 ' + r.session.slice(0, 8) + ')' : '';
    console.log('  [%s·%s] %s%s', r.action, r.tool, r.path, tail);
    if (r.hint !== null && r.hint !== undefined) console.log('        ↳ %s', r.hint);
  }
  if (facts.toolkit_root !== null) {
    const hidden = facts.outside_reads.filter(r => r.scope !== 'outside_toolkit' && r.action !== '写');
    const hiddenCounts = { harness_temp: 0, other_project: 0, toolkit_internal: 0 };
    for (const r of hidden) hiddenCounts[r.scope] += 1;
    console.log('  另 %d 条(harness_temp %d · other_project %d · toolkit_internal %d)—— 明细见 json',
      hidden.length,
      hiddenCounts.harness_temp, hiddenCounts.other_project, hiddenCounts.toolkit_internal);
  }
  for (const n of facts.log_notes) console.log('⚠️ 日志读取:%s', n);
  console.log('⚠️ 这是下限不是全集:%s', facts.note);
  // ⛔ 先打印再写盘:写盘失败不该把已经算出来的事实一起吞掉。
  return writeJson(jsonPath, facts);
}

process.exit(main(process.argv.slice(2)));
