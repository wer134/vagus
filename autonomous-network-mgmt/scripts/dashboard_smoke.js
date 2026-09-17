// 대시보드 렌더 스모크 테스트 (cowork/VISUALIZATION_PLAN.md §4 V-Phase 2)
//
// 이 리포는 pytest를 쓰지 않고 모듈마다 자가 테스트를 둔다. 대시보드는 파이썬 모듈이 아니므로
// 여기에 둔다 — scripts/selftest.sh가 node가 있을 때 이 파일을 돌린다.
//
// 검사하는 것:
//   1) dashboard_data.js의 실제 수치로 모든 차트가 예외 없이 그려지는가
//   2) 이중축을 쓴 차트가 없는가 (도판 규칙)
//   3) 계열이 2개 이상인데 범례를 끈 차트가 없는가 (도판 규칙)
//   4) 화면에 찍힌 KPI가 결과 파일의 값과 같은가 (손으로 적은 숫자가 섞이지 않았는가)
//   5) dashboard_data.js가 없을 때도 죽지 않고 안내 문구를 내는가
//
//   node scripts/dashboard_smoke.js
const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(ROOT + '/dashboard.html', 'utf8');

const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
const touched = new Set();
const mkEl = (id) => ({
  id, _html: '', _text: '', className: '', style: { setProperty(){} },
  set innerHTML(v){ touched.add(id); this._html = v; }, get innerHTML(){ return this._html; },
  set textContent(v){ touched.add(id); this._text = v; }, get textContent(){ return this._text; },
  classList: { toggle(){}, remove(){}, add(){} }, scrollTop: 0, scrollHeight: 0,
  parentElement: { dataset: {}, set innerHTML(v){}, get innerHTML(){ return ''; } },
});
const store = new Map();
global.document = {
  documentElement: { style: { setProperty(){} } },
  getElementById(id) { if (!ids.has(id)) return null;
    if (!store.has(id)) store.set(id, mkEl(id)); return store.get(id); },
  querySelectorAll() { return []; }, querySelector() { return null; },
};
const drawn = [];
global.Chart = class { constructor(el, cfg) { drawn.push({ el: el && el.id, cfg }); } destroy() {} };
global.window = { ANM_DATA: null };
global.setInterval = () => 0;
global.fetch = async () => { throw new Error('fetch 금지 — 이 페이지는 백엔드를 부르지 않는다'); };

// dashboard_data.js 적재
eval(fs.readFileSync(ROOT + '/dashboard_data.js', 'utf8'));

// 페이지 스크립트에서 자동 실행부(Init)를 뺀 본문만 평가
let body = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));
body = body.slice(0, body.indexOf('// ═══════════════════ Init ═══════════════════'));
eval(body);

applyPalette();
loadResults();
loadTraining();

console.log('그린 차트:', drawn.map(d => d.el).join(', '));
const must = ['chart-ttr','chart-offline','chart-abl-ttr','chart-abl-waste',
              'chart-entropy','chart-topshare','chart-policy'];
const missing = must.filter(m => !drawn.some(d => d.el === m));
if (missing.length) throw new Error('안 그려진 차트: ' + missing);

// 이중축 금지 — y축이 둘인 설정이 하나라도 있으면 실패
for (const d of drawn) {
  const sc = d.cfg.options && d.cfg.options.scales || {};
  const yAxes = Object.keys(sc).filter(k => k === 'y' || /^y\d/.test(k) || k === 'y1');
  if (yAxes.length > 1) throw new Error('이중축 발견: ' + d.el);
}

// 범례가 없는 차트는 계열이 1개여야 한다 (규칙: 계열 2개 이상이면 범례 필수)
for (const d of drawn) {
  const legendOff = d.cfg.options?.plugins?.legend?.display === false;
  const n = (d.cfg.data.datasets || []).length;
  if (legendOff && n > 1) throw new Error(`범례 없는 다계열 차트: ${d.el} (${n}계열)`);
}

// 핵심 수치가 화면에 실제로 들어갔는지
const kpi = store.get('kpi-ttr')._text, waste = store.get('kpi-waste')._text;
const D2 = window.ANM_DATA;
if (kpi !== D2.closed_loop.avg_ttr.toFixed(2)) throw new Error('KPI TTR 불일치: ' + kpi);
if (waste !== D2.closed_loop.wasted_per_ep.toFixed(2)) throw new Error('KPI 부수조치 불일치');
const ablTtr = D2.ablation.modes[0].avg_ttr.toFixed(2);
if (!store.get('abl-tbody')._html.includes(ablTtr)) throw new Error('절제 표에 결과 값이 없다: ' + ablTtr);
if (!store.get('ckpt-tbody')._html.includes('붕괴')) throw new Error('체크포인트 표가 비었다');
if (!store.get('prov-results')._html.includes('sim3')) throw new Error('조건 스탬프 없음');

console.log(`KPI TTR=${kpi}  부수조치=${waste}`);
console.log('갱신된 DOM 노드:', [...touched].sort().join(', '));

// Chart.js(CDN)를 못 불러왔을 때도 표와 수치는 남아야 한다.
// 그림이 실패하면 예외가 렌더 함수를 통째로 중단시켜 표까지 비는 일이 실제로 있었다.
const savedChart = global.Chart;
delete global.Chart; store.clear(); eval(body);
loadResults(); loadTraining();
if (!store.get('abl-tbody')._html.includes(D2.ablation.modes[0].avg_ttr.toFixed(2)))
  throw new Error('Chart.js 없이 절제 표가 비었다');
if (!store.get('ckpt-tbody')._html.includes('붕괴'))
  throw new Error('Chart.js 없이 체크포인트 표가 비었다');
if (store.get('kpi-ttr')._text !== D2.closed_loop.avg_ttr.toFixed(2))
  throw new Error('Chart.js 없이 KPI가 비었다');
console.log('Chart.js 부재 경로: 표·KPI 유지됨');
global.Chart = savedChart;

// 데이터가 없을 때도 죽지 않아야 한다
window.ANM_DATA = null; store.clear();
eval(body);
loadResults(); loadTraining();
console.log('데이터 없음 경로:', store.get('prov-results')._text.slice(0, 40) + '…');
console.log('\n모두 통과');
