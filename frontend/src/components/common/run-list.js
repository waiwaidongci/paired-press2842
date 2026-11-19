import {store} from '../../stores/research.js';
import {table} from './table.js';
import {status} from './status.js';
import {esc,snapshotCount} from '../../utils/format.js';
export function runList(){return table(['编号 / 运行','状态','名册快照','复核'],store.runs.map(r=>[{html:`<a href="#results/${r.id}">#${r.id} ${esc(r.name)}</a>`},{html:status(r.status)},snapshotCount(r.snapshot)+'席 · '+r.checksum.slice(0,12),r.review?'已复核':'待负责人复核']));}
