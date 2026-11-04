'use strict';
const byId = id => document.getElementById(id);
const numeric = ['demand_bias','observation_delay','delay_jitter','missing_probability','load_noise','propagation_delay','convergence_ticks'];
const booleans = ['background_via_source','initial_missing'];
const phaseLabels = {monitor:'감시',mitigate:'우회 적용',hold:'복귀 판단',restore:'원상복귀',verify:'완료 검증',completed:'완료 선언'};
const reasons = {monitoring:'과부하 감시 중',overload_detected:'과부하 감지 → 우회 명령',mitigation_wait:'우회 명령 적용 대기',hold_gate_wait:'현재 관측·정상 경로 예측 확인 중',restore_gate_passed:'복귀 조건 충족 → 정상 비용 복귀',restoration_wait:'원상복귀 명령 적용 대기',verification_wait:'복구 완료 증거 확인 중',completion_gate_passed:'제어기가 복구 완료를 선언함',completed:'완료 선언 후 실제 상태 추적 중'};
let options, session, busy = false, running = false, autoGeneration = 0;
const fmt = value => Number.isFinite(value) ? value.toFixed(2) : '—';
const ns = 'http://www.w3.org/2000/svg';
function svg(tag, attributes, text) {const item=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attributes)) item.setAttribute(key,String(value));if(text!==undefined)item.textContent=text;return item;}
async function request(path, method='GET', body) {
  const response=await fetch('/recovery'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){let message=`HTTP ${response.status}`;try{const data=await response.json();message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail);}catch{}throw new Error(message);}
  return response.status===204?null:response.json();
}
function showError(error){byId('error').textContent=error.message;byId('error').hidden=false;}
function controls(){byId('create').disabled=busy||!options;for(const id of ['step','finish'])byId(id).disabled=busy||!session||session.finished||running;byId('auto').disabled=!session||session.finished||(busy&&!running);byId('auto').textContent=running?'일시 정지':'자동 진행';byId('download').disabled=busy||!session;byId('status').textContent=session?.finished?'시뮬레이션 종료':running?'자동 진행 중':busy?'실행 중':'단계 실행 준비';}
async function execute(action){if(busy)return;busy=true;byId('error').hidden=true;controls();try{await action();}catch(error){running=false;showError(error);}finally{busy=false;controls();}}
function preset(){const selected=options.presets.find(p=>p.name===byId('regime').value);for(const name of numeric)byId(name).value=selected.values[name];for(const name of booleans)byId(name).checked=selected.values[name];}
async function create(){running=false;autoGeneration++;const overrides={};for(const name of numeric)overrides[name]=Number(byId(name).value);for(const name of booleans)overrides[name]=byId(name).checked;const next=await request('/sessions','POST',{policy:byId('policy').value,regime:byId('regime').value,seed:Number(byId('seed').value),overrides});const old=session;session=next;if(old)await request('/sessions/'+old.session_id,'DELETE');render();}
async function advance(ticks){session=await request(`/sessions/${session.session_id}/step`,'POST',{ticks});render();if(session.finished)running=false;}
function tableRow(values){const row=document.createElement('tr');for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}return row;}
function render(){
  const last=session.history.at(-1), selected=options.policies.find(p=>p.name===session.request.policy);
  byId('active').textContent=`${selected.label} · ${session.request.regime} · seed ${session.request.seed}`;
  byId('tick').textContent=`${session.tick} / ${session.horizon}`;
  byId('phase').textContent=last?phaseLabels[last.phase]:'대기';
  byId('commands').textContent=session.command_count;
  byId('safety').textContent=session.unsafe_restore?'위험한 복귀 발생':session.command_count<2?'아직 복귀 전':'관측 구간 내 안전';
  byId('safety').classList.toggle('danger',session.unsafe_restore);
  const sample=last?.observation;
  byId('sample').textContent=sample?`표본 tick ${sample.sampled_at} · 나이 ${session.tick-sample.sampled_at} tick · 보고 수요 ${fmt(sample.target_demand)}`:last?'이번 tick에 도착한 관측 없음':'첫 관측 대기';
  byId('diagnosis').textContent=last?`${reasons[last.reason]||last.reason} · 증거 tick [${last.evidence.join(', ')}]`:'제어기 판단을 기다립니다.';
  const calibration=last?.calibration;
  byId('calibration').textContent=calibration?`보정: ${calibration.enabled?(calibration.valid?'유효':'무효 / 대기'):'미사용'} · ${calibration.reason}${calibration.estimate===null?'':` · 추정 수요 ${fmt(calibration.estimate)}`}`:'보정 상태: 대기';
  byId('truth-state').textContent=last?`평가기 실제 상태: 수요 ${fmt(last.truth.target_demand)} · 수렴 ${last.truth.settled?'완료':'진행 중'} · 정상 비용 ${last.truth.restored?'복귀 완료':'미복귀'} · 완료 오판 ${session.false_completion?'있음':'없음'}`:'실제 상태: 대기';
  byId('links').replaceChildren();for(const link of last?.truth.links||[]){const observed=sample?.links.find(l=>l.edge===link.edge);byId('links').append(tableRow([link.edge,fmt(observed?.load),fmt(link.load),fmt(link.capacity),fmt(observed?.cost),fmt(link.cost)]));}
  const events=byId('changes-only').checked?session.history.filter((p,i,all)=>i===0||i===all.length-1||p.commands.length||p.phase!==all[i-1].phase||p.reason!==all[i-1].reason):session.history;
  byId('events').replaceChildren();for(const point of [...events].reverse())byId('events').append(tableRow([point.tick,phaseLabels[point.phase],reasons[point.reason]||point.reason,point.commands.join(', ')||'—']));
  network(sample);chart();controls();
}
function network(sample){
  const positions={a:[60,150],b:[260,45],c:[260,255],d:[460,150]};
  byId('edges').replaceChildren();byId('nodes').replaceChildren();
  for(const edge of ['a-b','a-c','b-d','c-d','b-c']){const [from,to]=edge.split('-'),[x1,y1]=positions[from],[x2,y2]=positions[to],link=sample?.links.find(l=>l.edge===edge),ratio=link?link.load/link.capacity:null;
    byId('edges').append(svg('line',{x1,y1,x2,y2,stroke:ratio===null?'var(--line)':ratio>1?'var(--bad)':'var(--ok)','stroke-width':4}));
    byId('edges').append(svg('text',{x:(x1+x2)/2+(edge==='b-c'?10:0),y:(y1+y2)/2-8,'text-anchor':edge==='b-c'?'start':'middle'},link?`${Math.round(ratio*100)}% / ${link.cost}`:'대기'));
  }
  for(const [name,[cx,cy]] of Object.entries(positions)){byId('nodes').append(svg('circle',{cx,cy,r:22,fill:'var(--bg)',stroke:'var(--accent)','stroke-width':2}),svg('text',{x:cx,y:cy+5,'text-anchor':'middle'},name));}
}
function chart(){
  const root=byId('chart');root.replaceChildren(svg('title',{id:'chart-title'},'시간에 따른 최대 링크 부하율'));
  const max=Math.max(1.6,...session.history.map(p=>p.truth.max_utilization),...session.history.map(p=>p.observation?.max_utilization||0));
  const x=t=>40+t/session.horizon*440,y=v=>220-v/max*185;
  root.append(svg('line',{x1:40,y1:y(1),x2:480,y2:y(1),stroke:'var(--muted)','stroke-dasharray':'5 4'}),svg('text',{x:42,y:y(1)-8},'100%'),svg('text',{x:40,y:246},'0'),svg('text',{x:455,y:246},`${session.horizon} tick`));
  for(const [getter,color] of [[p=>p.truth.max_utilization,'var(--warn)'],[p=>p.observation?.max_utilization,'var(--accent)']]){let path='',connected=false;for(const p of session.history){const value=getter(p);if(value===undefined){connected=false;continue;}path+=`${connected?'L':'M'} ${x(p.tick)} ${y(value)} `;connected=true;}root.append(svg('path',{d:path,fill:'none',stroke:color,'stroke-width':2}));}
}
byId('setup').addEventListener('submit',event=>{event.preventDefault();execute(create);});
byId('regime').addEventListener('change',()=>{if(options)preset();});
byId('changes-only').addEventListener('change',()=>{if(session)render();});
byId('step').addEventListener('click',()=>execute(()=>advance(1)));
byId('finish').addEventListener('click',()=>execute(()=>advance(120)));
byId('auto').addEventListener('click',async()=>{const generation=++autoGeneration;running=!running;controls();if(!running)return;while(running&&!session.finished&&generation===autoGeneration){await execute(()=>advance(1));if(running)await new Promise(resolve=>setTimeout(resolve,180));}controls();});
byId('download').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(session,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`recovery-${session.session_id}.json`;link.click();URL.revokeObjectURL(url);});
execute(async()=>{options=await request('/options');for(const p of options.policies){const option=document.createElement('option');option.value=p.name;option.textContent=p.label;byId('policy').append(option);}for(const p of options.presets){const option=document.createElement('option');option.value=p.name;option.textContent=p.label;byId('regime').append(option);}byId('policy').value='calibrated_forecast';byId('regime').value='demand_bias';preset();await create();});
