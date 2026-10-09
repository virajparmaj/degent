'use strict';
const stages = [
  ['prepare','Prepare','with intention.','Define the research.','Before the search.','Choose the companies, periods and limits for a reproducible run.'],
  ['discover','Discover','with evidence.','Find the past.','One capture at a time.','Archive indexes produce candidates. Eligibility comes after body recovery.'],
  ['recover','Recover','what survives.','From links to','usable evidence.','Retrieve bodies, check eligibility and resolve vacancy identity.'],
  ['extract','Extract','the requirements.','Read what','was required.','Every qualification starts with its exact source passage.'],
  ['review','Review','before trusting.','Make every','decision traceable.','Human approval stays separate from automated validation.'],
  ['compare','Compare','with context.','Observe changes.','Keep the uncertainty.','Compare captures of one vacancy, or shares across similar job samples.'],
  ['report','Report','what is supported.','Share the findings.','Keep the evidence.','Coverage, source anchors and limitations travel with every result.'],
];
const root = document.getElementById('app');
const S = {step:'prepare',run:'',detail:null,tab:1,page:0,search:'',company:'',period:'',kind:'',items:[],pages:1,total:0,selected:null,source:null,sourcePage:0,fieldPage:0,sourceFieldPage:0,compare:'changes',configTab:'scope',periodPage:0,exportPage:0,pending:false,category:'',statement_scope:'',status:'',unknown:false,importText:'',draft:null,reviewDrafts:new Map(),recordContexts:new Map(),documentId:null,busy:false,epoch:0,focusTile:null};
let D = null, noticeTimer, searchTimer, lastJob = '';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const human = value => String(value ?? 'Unknown').replaceAll('_',' ');
const fmt = n => Number(n || 0).toLocaleString();
const current = () => stages.find(s => s[0] === S.step);
const head = (name, extra='') => `<div class="tile-head"><span class="eyebrow">${esc(name)}</span>${extra}</div>`;
const pill = (name, color='') => `<span class="pill ${color}">${esc(name)}</span>`;
const row = (name,value) => `<div class="plain-row"><strong>${esc(name)}</strong><span>${esc(value ?? 'Unknown')}</span></div>`;
const spacer = '<div class="spacer"></div>';
const button = (label, action, value='', disabled=false, cls='solid-button') => `<button class="${cls}" data-action="${action}" data-value="${esc(value)}" ${disabled?'disabled':''}>${esc(label)}</button>`;
const jobActive = () => D?.job.status === 'running';
const activeHere = () => jobActive() && D.job.run_id === S.run;
const metric = (value,label) => `<div><div class="metric med">${fmt(value)}</div><div class="tiny">${esc(label)}</div></div>`;
const empty = (title,body) => `<div class="empty-state"><h2>${title}</h2><p>${esc(body)}</p></div>`;
const option = (value,label,selected) => `<option value="${esc(value)}" ${selected?'selected':''}>${esc(label)}</option>`;

async function api(path, payload) {
  const response = await fetch(path, payload ? {method:'POST',headers:{'Content-Type':'application/json','X-Degent-Token':D.token},body:JSON.stringify(payload)} : {});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'The local agent could not complete this action.');
  return data;
}
function url(path, args={}) { return `${path}?${new URLSearchParams({run:S.run,...args})}`; }
function notify(text) {
  const notice = document.getElementById('notice');
  notice.textContent = text; notice.classList.add('visible');
  clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.classList.remove('visible'), 5500);
}
function progress() {
  const index = stages.findIndex(s => s[0] === S.step);
  return `<div class="stage-marker" aria-label="Stage ${index+1} of 7">${stages.map((s,i) => `<span class="${i===index?'active':i<index?'done':''}"></span>`).join('')}</div>`;
}
function tile(tone,content,detail,label) {
  return `<section class="tile ${tone}" ${detail?`data-open="${detail}" tabindex="0" aria-label="${esc(label || 'Open '+detail)}"`:''}>${content}${detail?`<span class="icon-arrow" aria-hidden="true">↗</span>`:''}</section>`;
}
function operation(name,label) {
  let blocked = jobActive() || S.busy || !D.exists;
  if (name === 'discover') blocked ||= !D.discovery_left;
  if (name === 'fetch') blocked ||= !D.counts.candidates || !D.body_left;
  if (name === 'extract') blocked ||= !D.counts.eligible;
  if (name === 'current' && ['complete','bounded'].includes(D.collection?.status)) blocked=true;
  if (name === 'refresh' && D.collection && !['complete','bounded','needs_attention'].includes(D.collection.status)) blocked=true;
  return button(label,'operation',name,blocked);
}
function jobNote() {
  if (!activeHere() && D.workflow?.status) return `<div class="status-note">Research: ${esc(human(D.workflow.status))}${D.workflow.pause_reason ? ' · '+esc(human(D.workflow.pause_reason)) : ''}</div>`;
  if (!activeHere() && D.extraction_pause_reason) return `<div class="status-note error-note">Reading paused: ${esc(human(D.extraction_pause_reason))}. Saved evidence is available.</div>`;
  if (!D.job.status || D.job.run_id !== S.run) return '';
  if (activeHere()) return `<div class="job-line"><span class="job-dot"></span>${esc(human(D.job.operation))} is running · saved checkpoints update live</div>`;
  if (D.job.status === 'failed') return `<div class="status-note error-note">${esc(human(D.job.operation))} failed. Open this tile for the reason and retry controls.</div>`;
  if (D.job.status === 'stopped') return '<div class="status-note">Stopped. Resume from the saved checkpoints.</div>';
  return '';
}
function brandTile() {
  const stage = current(), index = stages.indexOf(stage);
  return tile('tone-blue brand-tile', `${head('Degent / historical research',`<span class="stagecount">0${index+1} / 07</span>`)}<div class="brand">degent<span class="brand-dot">.</span></div><h1 class="stage-hero">${esc(stage[1])}<br><span class="serif">${esc(stage[2])}</span></h1>${progress()}<div class="stage-footer"><select class="step-select" data-field="stage" aria-label="Choose a stage">${option('','Choose a stage',true)}${stages.map(s => option(s[0],s[1],false)).join('')}</select><div class="stage-navigator">${button('←','prev','',index===0,'nav-circle')}${button('→','next','',index===6,'nav-circle')}</div></div><div class="run-switch"><select data-field="run" aria-label="Current research run">${(D.runs.length?D.runs:[S.run]).map(id=>option(id,id,id===S.run)).join('')}</select>${button('+ New','new-run','',false,'mini-button')}</div>`,null);
}
function banner() {
  const stage = current();
  return tile('tone-ivory', `${head('The research process',pill('Local evidence','blue'))}${spacer}<h2 class="headline">${esc(stage[3])}<br><span class="serif">${esc(stage[4])}</span></h2>${spacer}<p class="context-note">${esc(stage[5])}</p>`, 'journey','Explore the seven research stages');
}
function logoStrip() { return `<div class="logo-strip">${D.config.employers.map(e=>`<span class="brand-small">${esc(e.name)}</span>`).join('')}</div>`; }
function contextTile() {
  const c = D.counts;
  const bodies = {
    prepare:`${head('Research scope')}${spacer}<h2 class="section-title">${D.config.employers.length} companies.<br><span class="serif">${D.config.roles.tracks.length} role tracks.</span></h2>${spacer}${logoStrip()}${spacer}<div class="scope-chips"><span class="scope-chip">US roles</span><span class="scope-chip">${esc((D.config.roles.entry_mid_levels||['entry','mid']).join(' + '))}</span><span class="scope-chip">${D.config.periods.length} periods</span></div><p class="context-note">New runs preserve their scope and request limits.</p>`,
    discover:D.config.study_version?`${head('Official inventory')}${spacer}<h2 class="section-title">Current postings.<br><span class="serif">Recorded coverage.</span></h2>${row('Discovered listings',D.collection?.counts?.discovered||0)}${row('Body captures',D.collection?.counts?.retrieved||0)}${button('Inventory / progress','open','collection',false,'muted-button')}`:`${head('Where we look')}${spacer}<h2 class="section-title">Two archives.<br><span class="serif">One evidence trail.</span></h2>${spacer}<div class="logo-strip"><span class="brand-small">Wayback</span><span class="brand-small">Common Crawl</span></div>${spacer}${row('Company paths',D.config.employers.length+' employers')}${row('Reporting periods',D.config.periods.length)}${row('Blocked queries',c.blocked)}<p class="tiny">Index matches are candidates only.</p>`,
    recover:`${head('Screening rules')}${spacer}<h2 class="section-title">Not every capture<br><span class="serif">is a job posting.</span></h2>${spacer}${row('Employer identity','Verify')}${row('Description body','Complete')}${row('US scope','Posting-specific')}${row('Vacancy identity','Group captures')}${button('Current screening decisions','open','selections',false,'muted-button')}${spacer}<p class="context-note">Failed retrievals and rejection reasons stay in the evidence trail.</p>`,
    extract:`${head('Document queue')}${spacer}${metric(c.documents,'accepted extraction documents')}${spacer}${D.preview_observations.filter(o=>o.body_complete&&o.eligible).slice(0,3).map(o=>`<div class="line-item"><span><strong>${esc(o.employer)}</strong><em>${esc(o.title)}</em></span><span class="tag">${esc(o.capture_date)}</span></div>`).join('')||'<p class="context-note">No complete eligible descriptions are ready. Inspect recovery exclusions before extracting.</p>'}${spacer}<span class="window-action">Browse source documents →</span>`,
    review:`${head('Needs human attention')}${spacer}${metric(c.pending,'review decisions still open')}${spacer}${row('Approved decisions',c.approved)}${row('Documents awaiting review',c.documents_pending_review||0)}${row('Source-linked mentions',c.requirements)}${row('Automated validation','Separate from approval')}${spacer}<span class="window-action">Open the review queue →</span>`,
    compare:`${head('Analysis modes')}${spacer}<h2 class="section-title">One vacancy.<br><span class="serif">Or similar job samples.</span></h2>${spacer}${row('Capture pairs',c.changes)}${row(`Groups with ${D.config.comparison_minimum||5}+ assessed jobs`,c.cohorts)}${row('Denominator','Distinct assessed vacancies')}${spacer}<div class="action-buttons">${button('Capture pairs','compare','changes',false,'muted-button')}${button('Skill shares','compare','cohorts',false,'muted-button')}${button('Combinations','compare','combinations',false,'muted-button')}${button('Exact wording','compare','wording',false,'muted-button')}</div>`,
    report:`${head('Local run bundle')}${spacer}<h2 class="section-title">Every result.<br><span class="serif">Its evidence intact.</span></h2>${spacer}${row('Available files',D.exports.length)}${row('Human decisions',c.approved)}${row('Eligible descriptions',c.eligible)}${spacer}<p class="context-note">Downloads contain your real run outputs, including coverage gaps and rejected observations.</p>`,
  };
  const detail = ({prepare:'config',discover:'paths',recover:'rules',extract:'documents',review:'queue',compare:'methods',report:'exports'})[S.step];
  return tile('tone-cream',bodies[S.step],detail);
}
function primary() {
  const c=D.counts;
  let body, detail='primary';
  if (S.step==='prepare') body=`${head('Run blueprint')}${spacer}<h2 class="big-title">Start with a<br><span class="serif">clear boundary.</span></h2><div class="scope-chips"><span class="scope-chip">${D.config.employers.map(e=>e.name).slice(0,2).join(' + ')}${D.config.employers.length>2?' + '+(D.config.employers.length-2):''}</span><span class="scope-chip">${D.method==='openrouter'?'OpenRouter':D.method==='provider_pending'?'Provider pending':D.method==='codex'?'Codex extraction':'Rule extraction'}</span><span class="scope-chip">US evidence</span></div>${spacer}${row('Reporting window',D.config.study_version?'Publication cohorts and capture periods':`${D.config.periods[0].start} — ${D.config.periods.at(-1).end}`)}${row('Partial periods',D.config.periods.filter(p=>p.kind==='partial').length+' separately flagged')}<div class="action-buttons">${button('Configure a new run →','new-run')}</div>`;
  if (S.step==='discover') body=D.config.study_version?`${head('Accessible listing inventory')}${spacer}<h2 class="big-title">${fmt(D.collection?.counts?.discovered||0)} <span class="serif">listings.</span></h2><p class="context-note">Official listing pages are enumerated before remaining descriptions. Changing inventories and unavailable bodies remain explicit.</p>${button('Inventory / progress','open','collection',false,'muted-button')}`:`${head('Archive search',pill('Indexes ≠ postings','blue'))}${spacer}<h2 class="big-title">${fmt(c.candidates)} <span class="serif">candidates.</span></h2><p class="long-text">Search history, outcomes and source paths stay together.</p>${spacer}${D.preview_queries.map(q=>`<div class="line-item"><span><strong>${esc(q.employer_id)} / ${esc(q.period_id)}</strong><em>${esc(human(q.source))} · ${esc(human(q.status))}</em></span><span class="tag">${fmt(q.records_found)} candidates</span></div>`).join('')||'<p class="context-note">Start discovery to search the configured archive paths.</p>'}${row('Requests, including retries',`${D.discovery_used} / ${D.config.discovery_request_cap}`)}${jobNote()}`;
  if (S.step==='recover') body=`${head('Body retrieval → eligibility',pill(c.rejected+' rejected',c.rejected?'warn':''))}${spacer}<h2 class="big-title">${fmt(c.eligible)} <span class="serif">eligible</span><br>observations.</h2>${spacer}<div class="number-grid">${[['attempts','Attempts'],['retrieved','Retrieved'],['vacancies','Vacancies']].map(([key,label])=>`<div class="number-cell"><div class="value">${fmt(c[key])}</div><div class="name">${label}</div></div>`).join('')}</div>${spacer}<p class="context-note">Several captures can belong to one vacancy. Only distinct openings enter cohort denominators.</p>${!c.eligible&&c.attempts?'<div class="status-note error-note">No retrieved body passed all gates. This run cannot support requirement comparisons.</div>':''}${jobNote()}`;
  if (S.step==='extract'||S.step==='review') {
    const r = D.preview_requirements.find(r=>S.step!=='review'||r.human_review!=='approved') || D.preview_requirements[0];
    body=`${head(S.step==='review'?'Source / review item':'Selected source evidence',pill(r?(r.human_review==='approved'?'Human approved':'Human pending'):'No eligible source',r?'blue':'warn'))}${r?`${spacer}<h2 class="section-title">A qualification starts<br><span class="serif">with its exact passage.</span></h2><div class="doc-visual"><div class="flexrow"><span class="eyebrow">${esc(r.employer)} · ${esc(r.concept)}</span>${pill(human(r.extraction_method))}</div><p class="doc-sentence"><span class="doc-highlight">${esc(r.phrase.slice(0,400))}</span></p><div class="evidence-meta"><span>Capture ${esc(r.capture_date)}</span><span>${esc(human(r.status))}</span></div></div>${spacer}<span class="window-action">Open source and structured fields →</span>`:D.config.study_version&&c.eligible?empty('Inference allowance<br><span class="serif">is pending.</span>',`${c.eligible} preserved observations are ready for section selection and review. No model inference is authorized.`):empty('Evidence comes<br><span class="serif">before extraction.</span>','No complete eligible descriptions have passed the recovery gates. Review excluded captures to see what prevented extraction.')}`;
  }
  if (S.step==='compare') body=`${head('Capture pairs / matched cohorts',pill(D.report_stale?'Rebuild needed':'Evidence-limited', 'warn'))}${c.changes||c.cohorts?`${spacer}<h2 class="big-title">Observe the<br><span class="serif">difference.</span></h2>${spacer}${row('Accepted capture changes',c.changes)}${row('Displayable cohorts',c.cohorts)}<p class="context-note">Inspect dated evidence before interpreting a difference.</p>${spacer}`:empty('A gap is<br><span class="serif">not a finding.</span>','This run has no supported capture changes or groups large enough for percentages. Build the analysis after eligible extraction.')}<div class="action-buttons">${operation('report','Build comparisons →')}</div>`;
  if (S.step==='report') body=`${head('Report preview',pill(D.report_stale?'Outdated · rebuild':D.exports.includes('report.json')?'Available locally':'Not built',D.report_stale?'warn':'blue'))}${spacer}<h2 class="big-title">The evidence,<br><span class="serif">not the guesswork.</span></h2>${spacer}<div class="number-grid">${[['vacancies','Distinct vacancies'],['changes','Capture changes'],['cohorts',`Groups with ${D.config.comparison_minimum||5}+ assessed jobs`]].map(([key,label])=>`<div class="number-cell"><div class="value">${fmt(c[key])}</div><div class="name">${label}</div></div>`).join('')}</div>${spacer}<p class="context-note">${c.eligible?'Coverage gaps, review state and partial periods remain explicit.':'No eligible descriptions passed screening. The report documents coverage and exclusions; it does not infer trends.'}</p>${jobNote()}`;
  return tile('tone-blue',body,detail);
}
function supporting() {
  const c=D.counts;
  const content = {
    discover:`${head('Source outcomes')}${spacer}<div class="flexrow">${metric(c.queries,'Queries completed')}${metric(c.blocked,'Blocked / failed')}</div>`,
    recover:`${head('Vacancy identity')}${spacer}<div class="flexrow">${metric(c.vacancies,'Distinct eligible openings')}<span class="window-action">Inspect groups →</span></div>`,
    extract:`${head('Requirements found')}${spacer}<div class="flexrow">${metric(c.requirements,'Source-linked mentions')}${pill(c.documents+' valid documents',c.documents?'green':'warn')}</div>`,
    review:`${head('Approval is separate')}${spacer}<div class="flexrow">${metric(c.approved,'Human-approved decisions')}${pill(c.pending+' pending',c.pending?'warn':'green')}</div>`,
    compare:`${head('Cohort method')}${spacer}<div class="flexrow"><div><div class="metric med">n ≥ ${D.config.comparison_minimum||5}</div><div class="tiny">For displayed percentages</div></div><span class="window-action">Inspect cohorts →</span></div>`,
  };
  return tile('tone-cream',content[S.step],S.step==='compare'?'cohorts':'supporting');
}
function readiness() {
  if (D.config.study_version) return D.doctor?.python_supported?'Local runtime ready':'Check local runtime';
  if (!D.doctor) return 'Not checked';
  const reachable=Object.values(D.doctor.archives||{}).filter(x=>x.reachable).length;
  return reachable===2?'Archives reachable':reachable?'One archive blocked':'Archives unavailable';
}
function statusTile() {
  const c=D.counts;
  const body = {
    prepare:`${head('Readiness')}${spacer}<h2 class="section-title">Check before<br><span class="serif">you run.</span></h2><p class="tiny">${esc(readiness())} · ${D.config.study_version?'Official current source':'Wayback / Common Crawl'}</p>`,
    discover:D.config.study_version?`${head('Collection allowance')}${row('Request-count limit','None')}${row('Downloaded allowance','200 MB initially')}${row('Active time allowance','Two cumulative hours')}`:`${head('Request budget')}${spacer}${metric(D.discovery_left,'Discovery requests left')}<div class="thin-track" style="margin-top:12px"><div class="thin-fill" style="width:${D.discovery_left/D.config.discovery_request_cap*100}%"></div></div>`,
    recover:D.config.collection_version===2?`${head('Inventory collection')}${spacer}${metric(D.collection?.counts?.retrieved||0,'Bodies captured')}${row('Session',human(D.collection?.status||'Ready'))}${row('Document limit',D.collection?.document_limit??'None')}${button('Progress / continue / refresh','open','collection',false,'muted-button')}`:(D.config.sources||[]).includes('current')&&!(D.config.sources||[]).includes('archives')?`${head('Current collection budget')}${spacer}<div class="metric med">${fmt(c.current_saved)}<span style="font-size:20px"> / ${D.config.current_document_cap}</span></div><p class="tiny">Saved document limit · ${Math.max(0,D.config.current_document_cap-(c.current_saved||0))} left</p><p class="tiny">Requests including retries: ${D.current_requests_used||0} / ${D.config.current_request_cap}</p>`:`${head('Archive bodies attempted')}${spacer}<div class="metric med">${fmt(c.attempts-(c.current_detail_attempts||0))}<span style="font-size:20px"> / ${D.config.body_attempt_cap}</span></div><p class="tiny">Archive retrieval cap · ${D.body_left} left</p>`,
    extract:`${head('Extraction mode')}${spacer}<h2 class="section-title">${D.method==='openrouter'?'OpenRouter.':D.method==='provider_pending'?'Provider pending.':D.method==='codex'?'Codex.':'Rules.'}</h2><p class="tiny">${D.method==='openrouter'?esc(D.config.inference.identity.model)+' · paid allowance required':D.method==='provider_pending'?'Selection and inference allowance are deferred':D.method==='codex'?esc(D.config.codex_model)+' · existing Codex login':'Deterministic, source-matched candidates'}</p>`,
    review:`${head('Next decision')}${spacer}<h2 class="section-title">${c.pending?'Verify the':'Queue is'}<br><span class="serif">${c.pending?'original wording.':'clear.'}</span></h2><p class="tiny">${c.requirements?'Human review is versioned and stored locally.':'No eligible requirements are awaiting review.'}</p>`,
    compare:`${head('Comparison mode')}${spacer}<h2 class="section-title">${S.compare==='changes'?'Capture':'Cohort'}<span class="serif"> ${S.compare==='changes'?'pairs.':'shares.'}</span></h2><p class="tiny">${S.compare==='changes'?'Capture dates are not employer edit dates.':'Employer, role, subtype and level stay separate.'}</p>`,
    report:`${head('Export state')}${spacer}<h2 class="section-title">${D.report_stale?'Rebuild':'Available'}<span class="serif"> ${D.report_stale?'needed.':'locally.'}</span></h2><p class="tiny">${D.exports.length} real run files · ${c.pending} decisions pending</p>`,
  };
  return tile('tone-blue2',body[S.step],'status');
}
function heatmap() {
  const years=[...new Set(D.config.periods.map(p=>(p.start||'').slice(0,4)))].filter(Boolean).slice(0,4);
  return `<div class="heat" style="grid-template-columns:60px repeat(${years.length},minmax(0,1fr))"><span></span>${years.map(y=>`<span class="label">${y}</span>`).join('')}${D.config.employers.map(e=>`<span class="label">${esc(e.name)}</span>${years.map(y=>{
    const rows=D.coverage.filter(r=>r.employer_id===e.id&&r.period_id.startsWith(y));
    const count=rows.reduce((n,r)=>n+r.records_found,0);
    const blocked=rows.some(r=>r.blocked_queries);
    const found=count>0;
    const cls=found?'found':blocked?'blocked':rows.some(r=>r.inventory_status!=='uninspected')?'empty':'unknown';
    return `<button class="cell ${cls}" data-action="coverage" data-value="${esc(e.id)}|${y}" aria-label="${esc(e.name)} ${y}: ${fmt(count)} index candidates${blocked?', blocked queries present':''}" title="${fmt(count)} candidates${blocked?' · partial or blocked':''}"></button>`;
  }).join('')}`).join('')}</div>`;
}
function actionTile() {
  const c=D.counts;
  let body;
  if(S.step==='prepare') body=`${head('Run action')}${spacer}<h2 class="section-title">Define. Check.<br><span class="serif">Then begin.</span></h2>${spacer}${row('Input sources',(D.config.sources||['archives']).join(' + '))}${row('Extraction method',human(D.method))}${row('Readiness',readiness())}${spacer}${operation('doctor','Check system →')}<div class="action-buttons">${operation('run','Run / resume research →')}</div><div class="action-buttons">${operation('current','Collect current jobs')}${D.config.study_version?'':button('Import descriptions','import-open','',jobActive(),'muted-button')}</div>${jobNote()}`;
  if(S.step==='discover') body=D.config.study_version?`${head('Inventory coverage')}${row('All pages enumerated',Object.values(D.collection?.sources||{}).every(s=>s.inventory_enumeration_complete)?'Yes; completeness/drift remains separate':'Pending')}${row('Collection status',human(D.collection?.status||'Ready'))}${button('Inspect inventory and limits','open','collection',false,'muted-button')}`:`${head('Company / year coverage')}${spacer}<h2 class="section-title">${D.config.employers.length} employers.<br><span class="serif">Different archives.</span></h2>${spacer}${heatmap()}${spacer}<p class="tiny">Blue = index candidates · stripes = blocked<br>Coverage here does not establish eligible evidence.</p>${operation('discover','Start / resume discovery →')}${jobNote()}`;
  if(S.step==='recover') body=`${head('Recovery outcomes')}${spacer}<h2 class="section-title">Inspect what<br><span class="serif">made the cut.</span></h2>${spacer}${row('Retrieved bodies',c.retrieved)}${row('Eligible captures',c.eligible)}${row('Rejected bodies',c.rejected)}${row('Failed attempts',c.failed)}${c.current_screened?row('Current titles screened',c.current_screened)+row('Rejected before retrieval',c.current_pre_rejected):''}${spacer}${D.config.study_version?operation('current','Continue current collection →'):operation('fetch','Start / resume recovery →')}${!D.body_left?'<p class="tiny">Body budget exhausted. Exclusions remain inspectable.</p>':''}${jobNote()}`;
  if(S.step==='extract') body=`${head('Structured mentions')}${spacer}<h2 class="section-title">Exact words.<br><span class="serif">Structured meaning.</span></h2>${spacer}${D.preview_requirements.map(r=>row(human(r.concept),human(r.status))).join('')||row('Eligible requirements','None yet')}${spacer}${operation('extract','Extract requirements →')}<p class="tiny">${c.eligible?'Accepted output still requires human review.':'Recovery must yield an eligible description first.'}</p>${jobNote()}`;
  if(S.step==='review') body=`${head('Correction')}${spacer}<h2 class="section-title">What does the<br><span class="serif">evidence say?</span></h2>${spacer}${row('Required / preferred','Source-supported')}${row('Unclear','Keep the ambiguity')}${row('Decision history','Append-only')}${spacer}${button('Open review workspace →','open','queue',!c.requirements)}<p class="tiny">Decisions persist locally and affect rebuilt reports.</p>`;
  if(S.step==='compare') body=`${head('Read the difference')}${spacer}<h2 class="section-title">A mention is<br><span class="serif">not a trend.</span></h2>${spacer}${row('Edit date','Unknown')}${row('Missing mention','Not proof of removal')}${row('Small samples','Percentages suppressed')}${row('Partial periods','No full-period difference')}${spacer}<div class="action-buttons">${button('Capture pairs','compare','changes',false,'muted-button')}${button('Skill shares','compare','cohorts',false,'muted-button')}${button('Combinations','compare','combinations',false,'muted-button')}${button('Exact wording','compare','wording',false,'muted-button')}</div>`;
  if(S.step==='report') body=`${head('Take the results')}${spacer}<h2 class="section-title">What the sources<br><span class="serif">can support.</span></h2>${spacer}${row('Coverage & limitations','Included')}${row('Source provenance','Preserved')}${row('Human approvals',c.approved+' / '+c.requirements)}${spacer}${operation('report',D.report_stale?'Rebuild reports →':'Build reports →')}<div class="action-buttons">${button('Download files ↓','open','exports',!D.exports.length,'muted-button')}</div>${jobNote()}`;
  if(activeHere()) body+=`<div class="action-buttons">${button('Stop operation','stop','',false,'muted-button')}</div>`;
  return tile('tone-ivory',body,'actions');
}
function compactTabs() {
  const labels=S.detail?['Records','Workspace','Evidence & actions']:['Stage & context','Research','Actions'];
  return `<nav class="compact-tabs" aria-label="Workspace panels">${labels.map((label,i)=>button(label,'compact',String(i),false,i===S.tab?'on':'')).join('')}</nav>`;
}
function mosaic() {
  const two=['prepare','report'].includes(S.step), evidence=['extract','review'].includes(S.step);
  root.innerHTML=`<main class="mosaic" aria-label="${esc(current()[1])} workspace"><div class="stack left ${S.tab===0?'compact-active':''}">${brandTile()}${contextTile()}</div><div class="stack center ${two?'two':evidence?'evidence-stack':''} ${S.tab===1?'compact-active':''}">${evidence?'':banner()}${primary()}${two?'':supporting()}</div><div class="stack right ${S.tab===2?'compact-active':''}">${statusTile()}${actionTile()}</div></main>${compactTabs()}`;
}

function draft() {
  if(!S.draft) S.draft={sources:['archives','current'],levels:[...(D.catalog.roles.entry_mid_levels||['entry','mid'])],comparison_review:'reviewed',comparison_status:'required_or_preferred',comparison_scope:'qualification',run_id:'research-'+new Date().toISOString().slice(0,10),employers:D.catalog.employers.map(e=>e.id),periods:D.catalog.periods.map(p=>p.id),tracks:[...D.catalog.roles.tracks],method:D.method,discovery_request_cap:300,body_attempt_cap:150,extract_document_cap:30,semantic_review_call_cap:10,current_request_cap:100,current_document_cap:null,collection_storage_cap:100000000,collection_byte_cap:50000000,collection_runtime_seconds:120,model_call_cap:40};
  return S.draft;
}
function companyVerificationEvidence(company) {
  const history=(company.verification_history||[]).filter(h=>h.attempt_id!==company.latest_verification_attempt);
  const successes=history.filter(h=>(h.sources||[]).some(s=>s.status==='verified'));
  return `<p class="tiny">Next action: ${esc(company.verification_remaining_action||'Check current evidence')}</p><details><summary>Latest evidence and source results</summary><pre class="tiny">${esc(JSON.stringify({attempt:company.latest_verification_attempt,evidence:company.verification_evidence||[],sources:(company.current_sources||[]).map(s=>({id:s.id,results:s.verification_results,ownership_evidence:s.ownership_evidence,samples:s.samples}))},null,2))}</pre></details><details><summary>Historical attempts (${history.length}); prior successful attempts: ${successes.length}</summary>${history.slice(-10).reverse().map(h=>`<p class="tiny">${esc(h.at||'Preserved legacy state')} · ${esc(h.attempt_id)}<br>${esc((h.sources||[]).map(s=>`${s.provider}: ${s.status} — ${s.verification_outcome||''}`).join('; '))}</p>`).join('')||'<p class="tiny">No earlier attempts.</p>'}</details>`;
}
function companyLibraryForm(d) {
  const all=D.catalog.employers;
  const local=all.filter(e=>[e.name,e.website,...(e.aliases||[])].join(' ').toLowerCase().includes((S.companyQuery||'').toLowerCase()));
  const result=S.companyResults||{items:local.slice((S.companyPage||0)*5,(S.companyPage||0)*5+5),page:S.companyPage||0,pages:Math.max(1,Math.ceil(local.length/5)),total:local.length};
  const c=S.companyDetail;
  return `${verificationPanel()}<span class="eyebrow">Local company library</span><div class="field"><label for="company-search">Search name, alias or website domain</label><input id="company-search" data-field="company-search" value="${esc(S.companyQuery||'')}"></div><div class="company-results">${(result.items||[]).map(e=>`<div class="company-result"><label><input type="checkbox" data-check="employers" value="${esc(e.id)}" ${d.employers.includes(e.id)?'checked':''}> ${esc(e.name)}</label><span>${esc(e.source_status||'configured')}</span>${button('Details','company-detail',e.id,false,'muted-button')}</div>`).join('')}</div>${pager('companies',result.page,result.pages)}<p class="tiny">${d.employers.length} selected: ${esc(d.employers.map(id=>all.find(e=>e.id===id)?.name||id).join(', '))}. Uncheck a company to remove it from this run.</p>${c?`<div class="company-detail"><strong>${esc(c.name)}</strong> ${pill(c.source_status||'configured')}<p class="tiny">${esc(c.website||'Website unknown')} · ${esc(c.group||'unknown')} · ${esc(c.provenance||'Local configuration')}</p>${(c.current_sources||[]).map(src=>`<div class="source-entry"><label><input type="checkbox" data-source="${esc(src.id)}" ${(d.source_ids||all.flatMap(e=>(e.current_sources||[]).map(s=>s.id))).includes(src.id)?'checked':''}> ${esc(src.provider)} · ${esc(src.status||'configured')}</label><p class="tiny">${esc(src.url||src.board)}<br>${esc(src.provenance||'Unknown provenance')}<br>${esc(src.last_verified_at||'Not verified yet')} · ${esc(src.verification_outcome||src.reason||'Verify before collection')}<br>Availability: ${esc(src.availability||'Unknown')} · Ownership: ${esc(src.ownership||'Unknown')}<br>Collector: ${esc(src.collector_capability||'Unknown')} · Sampling: ${esc(src.sampling_result||'Not checked')}<br>Inventory: ${esc(src.inventory_completeness||'Not checked')} · Latest attempt: ${esc(c.latest_verification_attempt||'Legacy state')}</p></div>`).join('')||'<p class="tiny">Source missing. Supply a supported career-board URL below using the same company name.</p>'}${companyVerificationEvidence(c)}${button('Verify / discover career sources','company-verify',c.id,S.busy,'muted-button')}</div>`:''}<details class="manual-company"><summary>Add company or another career source</summary>${[['name','Company name'],['website','Website URL'],['career_url','Career-board URL (optional)'],['aliases','Aliases (comma separated)'],['group','Business group (optional; unknown by default)']].map(([key,label])=>`<div class="field"><label for="company-add-${key}">${label}</label><input id="company-add-${key}" data-library="${key}" value="${esc(S.companyAdd?.[key]||'')}"></div>`).join('')}${button('Save to local library','company-add','',S.busy)}</details>`;
}
function verificationPanel() {
  const v=S.verification;
  return `<div class="company-detail"><span class="eyebrow">Source verification</span><p class="tiny">${v?`${esc(v.status)} · ${esc(v.processed)} / ${esc(v.total)} processed · ${esc(v.remaining)} remaining · ${esc(v.pending_checks||0)} checks pending`:'Check career sources independently of research.'}</p>${v?`<p class="tiny">${esc(JSON.stringify(v.counts||{}))}<br>Completed checks: ${esc(v.completed??v.processed)} · Deferred: ${esc(v.deferred||0)}<br>Requests: ${esc(v.usage?.requests||0)} / ${esc(v.budgets?.batch_requests||0)} · Bytes: ${fmt(v.usage?.bytes||0)} / ${fmt(v.budgets?.batch_bytes||0)}<br>Active time: ${esc(Math.ceil(v.usage?.seconds||0))} / ${esc(v.budgets?.batch_seconds||0)}s · Retry pass: ${esc(v.pass_progress?.completed||0)} / ${esc(v.pass_progress?.planned||0)}<br>${esc(v.directory||'')}</p>`:''}<div class="action-buttons">${button('Verify all library companies','verify-all','',jobActive(),'muted-button')}${v?button('Resume incomplete batch','verify-resume','',jobActive(),'muted-button')+button('Retry unfinished checks','verify-retry','',jobActive(),'muted-button'):''}${v?.status==='running'?button('Stop verification','verify-stop','',false,'muted-button'):''}</div>${(v?.results||[]).filter(r=>r.status!=='verified').slice(-3).map(r=>`<p class="tiny">${esc(r.name)}: ${esc(r.reason)} · ${esc(r.remaining_action)}</p>`).join('')}${S.verificationError?`<p class="tiny">${esc(S.verificationError)}</p>`:''}</div>`;
}
async function pollVerification() {
  const previous=S.verification;
  S.verification=await api('/api/company-verifications');
  if(previous?.status==='running'&&S.verification?.status!=='running') {
    if(S.companyDetail) S.companyDetail=await api('/api/company?'+new URLSearchParams({id:S.companyDetail.id}));
    S.companyResults=null;
  }
}
async function searchCompanies() {
  S.companyResults=await api('/api/companies?'+new URLSearchParams({search:S.companyQuery||'',page:S.companyPage||0,size:5}));render();
}
function studyPanel() {
  const metrics=(D.report.requirement_shares||[]).filter(r=>r.time_basis==='capture_period'&&r.statement_scope==='qualification');
  const cells=metrics.slice(0,100).map(r=>`<tr><td>${esc(r.role_group_id)}</td><td>${esc(r.level)}</td><td>${esc(r.concept)} · ${esc(r.requirement_status)}</td><td>${r.share_percent===null?'Unavailable':r.share_percent+'%'}</td><td>${r.count}/${r.assessed_vacancies} · ${r.unresolved_vacancies} unresolved · ${esc(r.assessment_basis)}</td></tr>`).join('');
  return `${head('Knowledge-role study')}<h2 class="detail-title">Comparable roles.<br><span class="serif">Preserved evidence.</span></h2>${row('Eligible distinct vacancies',D.report.distinct_vacancies??D.counts.vacancies)}${row('Role assignments pending',D.report.unassigned_vacancies??'Build report')}${row('Provider decision',D.extraction_pause_reason||'Not selected')}${row('Automation gate',D.report.automation_gate?.status||'Not evaluated')}<p class="context-note">Local similarity proposes groups from duties and domain context. Approve their definitions and exact evidence before comparison. Embeddings remain a future provider decision.</p><div class="action-buttons">${button('Prepare source units','study-action','prepare',S.busy||jobActive())}${button('Propose role clusters','study-action','groups',S.busy||jobActive())}${button('Freeze 60-document gold packet','study-action','gold',S.busy||jobActive())}${button('Build audit packet','study-action','audit-packet',S.busy||jobActive())}${button('Prepare failed-description retry','study-action','retry-failed',S.busy||jobActive())}</div><p class="tiny">Gold labels require the whole description: 20 calibration, 40 held-out. Download source units and proposals from Report → Download files. Human decisions and labels remain separate from automatic candidates.</p><div class="field"><label>Reviewed taxonomy or gold-label JSON</label><textarea data-field="study-json" rows="8">${esc(S.studyText||'')}</textarea></div><div class="action-buttons">${button('Approve reviewed taxonomy','study-action','approve-taxonomy',S.busy||jobActive())}${button('Evaluate held-out labels','study-action','evaluate',S.busy||jobActive())}${operation('report','Rebuild baseline report')}</div>${cells?`<div style="overflow:auto"><table><thead><tr><th>Role group</th><th>Seniority</th><th>Skill / strength</th><th>Prevalence</th><th>Assessment</th></tr></thead><tbody>${cells}</tbody></table></div><p class="tiny">First 100 cells. The complete heatmap and all cells are in role-baseline.html and CSV exports.</p>`:'<p class="context-note">No approved comparable groups yet. Percentages need 30 assessed vacancies per cell; unknowns remain unavailable.</p>'}`;
}
function collectionPanel() {
  const c=D.collection;
  if(!c) return `${head('Current descriptions')}<p class="context-note">Collect the available postings from your saved company sources. Research eligibility, extraction and review remain separate.</p>${operation('current','Collect now')}`;
  return `${head('Current collection',pill(human(c.status),'blue'))}<h2 class="detail-title">Capture now.<br><span class="serif">Keep the history.</span></h2><p class="context-note">${esc(human(c.pause_reason||'Inventory outcome below'))}</p>${row('Pending extraction',c.counts?.pending_extraction??0)}${row('Pending document review',c.counts?.pending_review??0)}${fields(c.counts||{},S.fieldPage,'fields')}<div class="collection-sources">${Object.values(c.sources||{}).map(i=>`<div class="source-entry"><strong>${esc(i.company_id)} · ${esc(i.source.provider)}</strong><p class="tiny">${esc(human(i.status))} · ${i.discovered} discovered · ${i.retrieved} bodies · ${i.eligible} eligible<br>Inventory: ${i.inventory_complete?'complete':i.inventory_bounded?'bounded':'unfinished'}<br>${esc(i.reason||i.selection_note||'')}${Object.keys(i.restrictions||{}).length?'<br>Source restrictions: '+esc(JSON.stringify(i.restrictions)):''}</p></div>`).join('')}</div><p class="tiny">Requests ${c.usage.requests} / ${c.budgets.requests??'uncapped'}; bytes ${fmt(c.usage.bytes)} / ${fmt(c.budgets.bytes)}; saved bytes ${fmt(c.usage.storage)} / ${fmt(c.budgets.storage)}; time ${Math.ceil(c.usage.seconds)} / ${c.budgets.seconds}s. Optional document limit: ${c.document_limit??'none'}.</p><div class="action-buttons">${operation('current','Continue collection')}${operation('refresh','Refresh with a new capture')}${operation('extract','Extract saved descriptions')}</div><details><summary>Extend this session allowance</summary><p class="tiny">Usage remains cumulative. Enter only the additional resources you authorize.</p>${['requests','bytes','seconds','storage','documents','model_calls','extraction_documents','semantic_review_calls'].map(k=>`<div class="field"><label for="extension-${k}">Additional ${esc(human(k))}</label><input id="extension-${k}" type="number" min="1" data-extension="${k}" value="${S.extensions?.[k]||''}"></div>`).join('')}${button('Record budget extension','collection-extend','',S.busy)}</details><p class="tiny">Changed company/source scope requires a new run. ${button('Create scope continuation','scope-continuation','',S.busy,'muted-button')}</p>`;
}
function configCenter() {
  const d=draft();
  let content='';
  if(S.configTab==='scope') content=`<div class="field"><label for="new-run-name">New run name</label><input id="new-run-name" data-draft="run_id" maxlength="80" value="${esc(d.run_id)}" required pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,79}"></div><p class="tiny">Existing run files are preserved. These choices apply to the new run.</p>${companyLibraryForm(d)}<p class="eyebrow" style="margin-top:23px">Role tracks</p><div class="field-checks">${D.catalog.roles.tracks.map(t=>`<label><input type="checkbox" data-check="tracks" value="${esc(t)}" ${d.tracks.includes(t)?'checked':''}>${esc(human(t).replace('quantitative credit risk capital modeling','Quantitative risk / capital'))}</label>`).join('')}</div>`;
  if(S.configTab==='periods') {
    const pages=Math.ceil(D.catalog.periods.length/4); S.periodPage=Math.min(S.periodPage,pages-1);
    content=`<p class="context-note">Completed and partial reporting windows stay distinct. Select the periods for this run.</p><div class="field-checks" style="flex-direction:column">${D.catalog.periods.slice(S.periodPage*4,S.periodPage*4+4).map(p=>`<label><input type="checkbox" data-check="periods" value="${esc(p.id)}" ${d.periods.includes(p.id)?'checked':''}><span><strong>${esc(p.id)}</strong><br><span class="tiny">${esc(p.start)} — ${esc(p.end)} · ${esc(p.kind)}</span></span></label>`).join('')}</div>${pager('period',S.periodPage,pages)}<p class="tiny">${d.periods.length} periods selected</p>`;
  }
  if(S.configTab==='budgets') content=`<p class="context-note">Collect the available inventory within this scope. Leave the document limit empty. Resource allowances pause work visibly; extensions are recorded separately.</p><div class="field-grid">${[['current_document_cap','Optional document limit',null],['current_request_cap','Collection requests',10000],['collection_byte_cap','Download bytes',1000000000],['collection_storage_cap','Saved evidence bytes',1000000000],['collection_runtime_seconds','Collection time (seconds)',3600],['discovery_request_cap','Archive discovery requests',300],['body_attempt_cap','Archive body attempts',150],['extract_document_cap','Extraction documents',30],['model_call_cap','Model calls',1000],['semantic_review_call_cap','Semantic review calls',10]].map(([key,label,max])=>`<div class="field"><label for="${key}">${label}</label><input id="${key}" type="number" min="1" ${max?`max="${max}"`:''} data-draft="${key}" value="${d[key]??''}"></div>`).join('')}</div><div class="field"><label for="extraction-method">Extraction method</label><select id="extraction-method" data-draft="method">${option('rules','Deterministic rules',d.method==='rules')}${option('codex','Codex · existing local login',d.method==='codex')}</select></div><p class="tiny">Collection can finish with extraction and review pending. Codex: ${esc(D.config.codex_model)}.</p>`;
  if(S.configTab==='sources') content=`<p class="context-note">Use available descriptions while keeping historical and current evidence separate.</p><div class="field-checks">${[['archives','Historical archives'],['current','Current public job feeds'],['imports','Saved descriptions']].map(([v,label])=>`<label><input type="checkbox" data-check="sources" value="${v}" ${d.sources.includes(v)?'checked':''}>${label}</label>`).join('')}</div><p class="eyebrow">Seniority</p><div class="field-checks">${['entry','mid','senior','management','executive'].map(v=>`<label><input type="checkbox" data-check="levels" value="${v}" ${d.levels.includes(v)?'checked':''}>${human(v)}</label>`).join('')}</div><p class="tiny">Current collection supports Greenhouse, Ashby, Lever, DOM/structured sources, Capital One and Workday. Verify company sources in the library. A separate current observation period is added automatically; current-only runs can clear historical periods. Unsupported sources are reported explicitly. Select Senior explicitly for Senior Business Analyst or Senior Associate titles; quantitative credit modeling is a separate track.</p>`;
  if(S.configTab==='scope') content+=`<p class="context-note">Capital One knowledge-role study: US roles across levels, full inventory, 200 MB / two hours / 1 GB resumable allowances. Model inference waits for provider selection.</p>${button('Create Capital One knowledge study','create-study','',S.busy)}`;
  if(S.configTab==='comparisons') content=`<p class="context-note">Choose what the resulting numbers mean. Unknown and unreviewed items remain visible.</p>${[['comparison_review','Evidence review',[['reviewed','Human-reviewed descriptions'],['accepted','Automated candidates — exploratory only']]],['comparison_status','Requirement strength',[['required_or_preferred','Required or preferred'],['required','Required only'],['preferred','Preferred only'],['stated','Explicitly stated, any strength']]],['comparison_scope','Where the statement applies',[['qualification','Applicant qualifications'],['responsibility','Job responsibilities'],['company_expectation','Company-wide expectations']]]].map(([key,label,values])=>`<div class="field"><label>${label}</label><select aria-label="${label}" data-draft="${key}">${values.map(([v,l])=>option(v,l,d[key]===v)).join('')}</select></div>`).join('')}`;
  return `${head('Configure a local research run')}<h2 class="detail-title">Make the research<br><span class="serif">reproducible.</span></h2><div class="config-sections">${['scope','periods','sources','budgets','comparisons'].map(t=>button(human(t),'config-tab',t,false,t===S.configTab?'on':'')).join('')}</div>${content}${spacer}<div class="action-buttons">${button('Create local run →','create-run','',S.busy)}</div>`;
}
function pager(action,page,pages) { return `<div class="pager">${button('← Prev',action+'-prev','',page===0,'') }<span>${page+1} / ${pages}</span>${button('Next →',action+'-next','',page>=pages-1,'')}</div>`; }
function filters() {
  return `<div class="filters"><input id="record-search" data-field="search" value="${esc(S.search)}" placeholder="Search records…" aria-label="Search records"><select data-field="company" aria-label="Filter employer">${option('','All companies',!S.company)}${D.config.employers.map(e=>option(e.id,e.name,e.id===S.company)).join('')}</select><select data-field="period" aria-label="Filter period">${option('','All periods',!S.period)}${D.config.periods.map(p=>option(p.id,p.id,p.id===S.period)).join('')}</select></div>${S.kind==='requirements'?`<div class="filters"><select data-field="category" aria-label="Requirement category">${option('','All skill categories',!S.category)}${['technical','domain','people_skills','work_habits','ai_use','ai_engineering','tool','education','experience'].map(v=>option(v,human(v),S.category===v)).join('')}</select><select data-field="statement_scope" aria-label="Statement context">${option('','All contexts',!S.statement_scope)}${['qualification','responsibility','company_expectation','company_description','hiring_process','unknown'].map(v=>option(v,human(v),S.statement_scope===v)).join('')}</select><select data-field="status" aria-label="Requirement strength">${option('','All strengths',!S.status)}${['required','preferred','not_required','unclear'].map(v=>option(v,human(v),S.status===v)).join('')}</select></div><label class="tiny"><input type="checkbox" data-field="unknown" ${S.unknown?'checked':''}> Unfamiliar wording only</label>`:''}`;
}
function recordName(r) {
  return r.concept?human(r.concept):r.title||r.path||r.original_url||r.vacancy_id||r.id;
}
function recordMeta(r) { return [r.employer||r.employer_id,r.period_id||r.period,r.status||r.body_status].filter(Boolean).map(human).join(' · '); }
function recordLeft() {
  return `${head('Degent / '+current()[1],pill('Local','blue'))}<div style="margin-top:16px">${button('← Back to mosaic','close','',false,'back')}</div><h2 class="detail-title">${({queries:'Archive queries',captures:'Recovered captures',selections:'Current job screening',requirements:S.step==='review'?'Review queue':'Requirements',changes:'Capture changes',cohorts:'Skill shares',combinations:'Skill combinations',wording:'Exact wording',coverage:'Coverage'})[S.kind]||'Research records'}</h2>${filters()}${S.step==='review'?`<label class="tiny" style="margin-top:9px"><input type="checkbox" data-field="pending" ${S.pending?'checked':''}> Pending decisions only</label>`:''}<div class="detail-list">${S.items.map(r=>`<button class="detail-item ${S.selected?.id===r.id?'current':''}" data-action="select" data-value="${esc(r.id)}"><span><strong>${esc(recordName(r))}</strong><small>${esc(recordMeta(r))}</small></span><span aria-hidden="true">→</span></button>`).join('')||'<p class="context-note">No records match these filters.</p>'}</div>${spacer}${pager('records',S.page,S.pages)}<p class="mini">${fmt(S.total)} matching records · ${esc(S.run)}</p>`;
}
function fields(data, page, action, size=4) {
  const entries=Object.entries(data).filter(([,v])=>v!==undefined).flatMap(([key,value])=>{
    const text=value===null?'Unknown':typeof value==='object'?JSON.stringify(value):String(value);
    const chunks=text.match(/[\s\S]{1,90}/g)||[''];
    return chunks.map((chunk,i)=>[chunks.length>1?`${key} (${i+1}/${chunks.length})`:key,chunk]);
  });
  const pages=Math.max(1,Math.ceil(entries.length/size)); page=Math.min(page,pages-1);
  return `<div class="detail-fields">${entries.slice(page*size,page*size+size).map(([key,value])=>`<div class="detail-data-row"><span class="lhs">${esc(human(key))}</span><span class="value" title="${esc(value)}">${esc(value)}</span></div>`).join('')}</div>${pages>1?pager(action,page,pages):''}`;
}
function sourcePanel(record) {
  if(!S.source) return `<div class="source-panel">${empty('Source not available','Select a recovered observation with preserved source text.')}</div>`;
  const evidence=record.evidence||'';
  let text=esc(S.source.text);
  if(evidence&&S.source.text.includes(evidence)) text=text.replace(esc(evidence),`<mark>${esc(evidence)}</mark>`);
  return `<div class="source-panel">${head('Preserved source text',pill(S.source.verified?'Hash verified':'Hash mismatch',S.source.verified?'green':'warn'))}<div class="source-text">${text||'No preserved source text is available.'}</div>${pager('source',S.source.page,S.source.pages)}${S.source.history?.length?`<details><summary>${S.source.history.length} captures of this vacancy</summary>${S.source.history.map(h=>`<p class="tiny">${esc(h.current_capture_at)} · ${esc(h.content_change)}<br>${esc(h.document_id)}<br>First observed: ${esc(h.collector_first_observed_at)}</p>`).join('')}</details>`:''}</div>`;
}
function recordCenter() {
  const r=S.selected;
  if(!r) return `${head('Evidence workspace')}${empty('Nothing to<br><span class="serif">invent here.</span>',S.kind==='requirements'?'No accepted eligible requirements are available. Inspect the recovery exclusions first.':'There are no matching records. Change filters or complete the previous stage.')}`;
  if(S.kind==='requirements'||(S.kind==='captures'&&r.observation_id)) {
    return `${head(S.kind==='requirements'?'Exact evidence / structured mention':'Recovered source / screening')}<h2 class="detail-title">${esc(r.employer||r.employer_id)}.<br><span class="serif">${esc(S.kind==='requirements'?human(r.concept):r.title)}</span></h2><p class="context-note">Capture ${esc(r.capture_date||'unknown')} · ${esc(r.document_id||'')}<br>${esc(S.kind==='requirements'?human(r.status):human(r.body_status))}</p>${sourcePanel(r)}${S.kind==='requirements'?`<p class="tiny">Exact matched evidence: <strong>${esc(r.evidence?.slice(0,140)||'No evidence anchor')}</strong></p>`:''}`;
  }
  if(S.kind==='queries') return `${head('Query outcome / preserved history')}<h2 class="detail-title">${esc(r.employer_id)}.<br><span class="serif">${esc(human(r.source))}</span></h2><p class="context-note">${esc(r.path)}</p>${fields({status:r.status,http_status:r.http_status,candidates_found:r.records_found,period:r.period_id,queried_at:r.queried_at,collection:r.collection,start:r.start,end:r.end,network_attempts:r.network_events?.length||0,resume_key:r.resume_key||'None'},S.fieldPage,'fields')}${spacer}<p class="context-note">A successful empty search is different from a blocked or unqueried path. Index rows have not passed the body or eligibility gates.</p>`;
  if(S.kind==='changes') return `${head('Same vacancy / two dated captures')}<h2 class="detail-title">Between captures.<br><span class="serif">Not an edit timestamp.</span></h2>${fields({vacancy:r.vacancy_id,before_capture:r.before_capture,after_capture:r.after_capture,added:r.added,no_longer_stated:r.no_longer_stated,modified:r.modified,employer_edit_date:r.edit_date},S.fieldPage,'fields')}${spacer}<p class="context-note">A missing mention does not establish that an employer removed the requirement.</p>`;
  return `${head(S.kind==='cohorts'?'Comparable jobs / sample size':'Record details')}<h2 class="detail-title">${esc(recordName(r))}<br><span class="serif">${esc(S.kind==='cohorts'?'Evidence in context.':'Original record.')}</span></h2>${fields(r,S.fieldPage,'fields')}${spacer}${S.kind==='cohorts'?`<p class="context-note">Shares are suppressed below ${D.config.comparison_minimum||5} assessed distinct vacancies. Partial periods have no full-period percentage-point comparison.</p>`:''}`;
}
function reviewDraft(record) {
  if(!S.reviewDrafts.has(record.id)) S.reviewDrafts.set(record.id,{status:record.status,concept:record.concept||'',statement_scope:record.statement_scope||'qualification',excluded:Boolean(record.excluded),note:record.review_note||'',revision:record.revision});
  return S.reviewDrafts.get(record.id);
}
function recordRight() {
  const r=S.selected;
  if(!r) return `${head('Evidence & actions')}${empty('A stage can finish<br><span class="serif">without enough evidence.</span>','Inspect recovery exclusions, preserve unavailable periods and avoid unsupported conclusions.')}<div class="action-buttons">${S.step==='extract'?operation('extract','Extract requirements'):S.step==='compare'?operation('report','Build comparisons'):button('Inspect recovery','stage','recover',false,'muted-button')}</div>`;
  if(S.step==='review'&&S.kind==='requirements') {
    const d=reviewDraft(r);
    return `${head('Human decision',pill(r.human_review==='approved'?'Approved':'Pending',r.human_review==='approved'?'green':'warn'))}<h2 class="detail-title">What does the<br><span class="serif">evidence say?</span></h2>${spacer}<div class="review-options">${['required','preferred','not_required','unclear'].map(v=>`<button data-action="review-status" data-value="${v}" aria-pressed="${d.status===v}">${esc(human(v))}</button>`).join('')}</div><div class="field-grid"><div class="field"><label>Meaning</label><select aria-label="Requirement meaning" data-field="review-concept">${option('','Unresolved wording',!d.concept)}${Object.entries(D.vocabulary||{}).map(([id,v])=>option(id,v.label,id===d.concept)).join('')}</select></div><div class="field"><label>Applies to</label><select aria-label="Where the statement applies" data-field="review-scope">${['qualification','responsibility','company_expectation','company_description','hiring_process','unknown'].map(v=>option(v,human(v),d.statement_scope===v)).join('')}</select></div></div><label class="tiny"><input type="checkbox" data-field="review-excluded" ${d.excluded?'checked':''}> Exclude this unsupported statement</label><div class="field"><label for="review-note">Reason / reviewer note</label><textarea id="review-note" data-field="review-note" maxlength="240" placeholder="Explain the supported interpretation…">${esc(d.note)}</textarea></div><p class="tiny">${r.human_review==='approved'?'Revision '+r.revision+' is saved locally.':'Automatic validation passed. Human approval is pending.'}</p><p class="tiny">${esc(r.review_reason||'Check the original text and proposed meaning.')}</p>${spacer}${button('Save human approval →','save-review','',S.busy||jobActive()||!S.source?.verified)}<p class="mini">Saved with source hash, timestamp and revision. Rebuild reports after a correction.</p>`;
  }
  const metadata=S.kind==='requirements'?{concept:r.concept,status:r.status,phrase:r.phrase,evidence:r.evidence,category:r.category,statement_scope:r.statement_scope,match_basis:r.match_basis,task:r.task,tools:r.tools,review_reason:r.review_reason,extraction_method:r.extraction_method,human_review:r.human_review,explicit:r.explicit,negated:r.negated,experience_years:r.experience_years,alternatives:r.alternatives,qualifier:r.qualifier,section:r.section}:S.kind==='queries'?{source:r.source,status:r.status,archive_collection:r.collection,query_id:r.query_id,network_events:r.network_events}:['cohorts','combinations','wording'].includes(S.kind)?{eligible_vacancies:r.eligible_vacancies,extracted_vacancies:r.extracted_vacancies,reviewed_vacancies:r.reviewed_vacancies,assessed_vacancies:r.assessed_vacancies,unresolved_vacancies:r.unresolved_vacancies,unavailable_reason:r.unavailable_reason,requirement_count:r.count,share_percent:r.share_percent===null?'Unavailable — see reason':r.share_percent,difference_percentage_points:r.difference_percentage_points,period_kind:r.period_kind}:S.kind==='changes'?{before_observation:r.before_observation_id,after_observation:r.after_observation_id,edit_date:r.edit_date}:{status:r.body_status||r.status,role:r.role_classification,geography:r.geography_status,geography_evidence:r.geography_evidence,selection_reason:r.selection_reason,rejection_reason:r.reason,classification:r.classification,publication_date:r.stated_publication_at,publication_date_source:r.publication_date_source,archive_capture:r.archive_capture_at||(r.archive_url?r.capture_at:null),current_capture:r.current_capture_at,captured:r.capture_at,requisition_id:r.requisition_id||r.source_posting_id,location:r.location,retrieved:r.retrieved_at,first_observed:r.collector_first_observed_at,vacancy_id:r.vacancy_id,source_sha256:r.clean_sha256};
  return `${head('Provenance & structured fields')}<h2 class="section-title" style="margin-top:24px">Keep the<br><span class="serif">source in view.</span></h2>${fields(metadata,S.sourceFieldPage,'provenance')}${spacer}${sourceLinks(r)}${documentReviewControls(r)}${S.kind==='requirements'?button('Review this requirement →','go-review','',false,'muted-button'):''}<p class="mini">Publication, capture, retrieval and first observation are separate timestamps.</p>`;
}
function documentReviewControls(r) {
  if(!r.document_id) return '';
  if(!D.config.study_version) return button('Approve reviewed document','approve-document','',S.busy||jobActive()||!S.source?.verified,'muted-button');
  if(S.docReview?.document_id!==r.document_id) S.docReview={document_id:r.document_id,omission_audited:false,description_completeness:'complete',selection_review:'complete',additions:'[]'};
  const d=S.docReview;
  return `<details><summary>Full-text omission review</summary><p class="tiny">Review the entire preserved description, including omitted requirements. Completeness means all available description text was captured; it does not prove employer importance.</p><label><input type="checkbox" data-doc-review="omission_audited" ${d.omission_audited?'checked':''}> I checked the full text for missed statements</label>${['description_completeness','selection_review'].map(k=>`<div class="field"><label>${human(k)}</label><select data-doc-review="${k}">${['complete','partial'].map(v=>option(v,human(v),d[k]===v)).join('')}</select></div>`).join('')}<div class="field"><label>Omitted statements (compact JSON; source unit IDs in source-units export)</label><textarea data-doc-review="additions">${esc(d.additions)}</textarea></div>${button('Save full-text review','approve-document','',S.busy||jobActive()||!S.source?.verified,'muted-button')}</details>`;
}
function sourceLinks(r) {
  return [['Original posting',r.original_url||r.source_url],['Archive capture',r.archive_url],['Earlier source',r.before_source_url],['Later source',r.after_source_url]].filter(([,v])=>/^https?:\/\//.test(v||'')).map(([label,v])=>`<a class="source-link" href="${esc(v)}" target="_blank" rel="noreferrer noopener">${esc(label)} ↗</a>`).join('');
}
function staticLeft() {
  return `${head('Degent / '+current()[1],pill('Local','blue'))}<div style="margin-top:18px">${button('← Back to mosaic','close','',false,'back')}</div>${spacer}<h2 class="detail-title">${S.detail==='config'?'Research scope.':S.detail==='exports'?'Local results.':S.detail==='journey'?'Seven stages.':'Research context.'}<br><span class="serif">One evidence trail.</span></h2>${spacer}<p class="context-note">${esc(current()[5])}</p><p class="tiny">${esc(S.run)}</p>${progress()}<div class="action-buttons">${button('← Previous','prev','',S.step==='prepare','muted-button')}${button('Next →','next','',S.step==='report','muted-button')}</div>`;
}
function staticCenter() {
  if(S.detail==='activity') return `${head('Recent actions and remaining limits')}<h2 class="detail-title">What happened.<br><span class="serif">What comes next.</span></h2>${fields({research_status:D.workflow?.status||D.extraction_status||'Not started',current_stage:D.workflow?.stage||'Unknown',pause_reason:D.extraction_pause_reason||'None',model_calls:`${D.model_calls_used||0} / ${D.config.model_call_cap||40}`,current_requests:`${D.current_requests_used||0} / ${D.config.current_request_cap||12}`,source_outcomes:D.current_sources||{},recent_actions:(D.activity||[]).map(a=>({at:a.at,stage:a.stage,action:a.action,document_id:a.document_id})),extraction_issues:D.extraction_issues||[]},S.fieldPage,'fields')}${spacer}<p class="tiny">Activity describes completed or attempted work. Missing evidence remains a gap.</p>`;
  if(S.detail==='import') return `${head('Import saved descriptions')}<h2 class="detail-title">Bring your<br><span class="serif">source text.</span></h2><p class="context-note">Choose a JSON file containing a list of postings, or paste it below. Required fields: employer_id, title, location, source_url, text. Optional captured_at needs a timezone and capture_date_source; otherwise the text stays undated.</p><div class="field"><label for="posting-file">JSON file · maximum 5 MB</label><input id="posting-file" type="file" accept=".json,application/json" data-field="import-file"></div><div class="field"><label for="import-text">Descriptions</label><textarea id="import-text" data-field="import-text" rows="6">${esc(S.importText)}</textarea></div>${spacer}${button('Import descriptions','import-save','',S.busy||jobActive())}`;
  if(S.detail==='study') return studyPanel();
  if(S.detail==='collection') return collectionPanel();
  if(S.detail==='config') return configCenter();
  if(S.detail==='journey') return `${head('Process map')}<h2 class="detail-title">One current stage.<br><span class="serif">Everything in reach.</span></h2><div class="detail-list">${stages.map((s,i)=>`<button class="detail-item ${S.step===s[0]?'current':''}" data-action="stage" data-value="${s[0]}"><span><strong>0${i+1} · ${esc(s[1])}</strong><small>${esc(s[5])}</small></span><span>→</span></button>`).join('')}</div>`;
  if(S.detail==='exports') {
    const names=D.exports.slice(S.exportPage*5,S.exportPage*5+5);
    return `${head('Download actual run files',pill(D.report_stale?'Outdated · rebuild':'Local outputs',D.report_stale?'warn':'blue'))}<h2 class="detail-title">Provenance<br><span class="serif">travels with results.</span></h2><div class="export-list">${names.map(name=>`<a href="${esc(url('/api/export',{file:name}))}" download="${esc(name)}"><span>${esc(name)}</span><span>↓</span></a>`).join('')||'<p class="context-note">Build a report to create downloadable outputs.</p>'}</div>${pager('exports',S.exportPage,Math.max(1,Math.ceil(D.exports.length/5)))}${spacer}<div class="action-buttons">${operation('report',D.report_stale?'Rebuild outputs →':'Build outputs →')}</div>`;
  }
  if(S.step==='prepare'||S.detail==='status') {
    const doc=D.doctor;
    const values=doc?{Python:doc.python,Wayback:doc.archives?.wayback,Common_Crawl:doc.archives?.commoncrawl_catalogue,Codex:doc.codex,configured_companies:doc.configured_employers,configured_periods:doc.configured_periods}: {readiness:'Not checked',sources:D.config.study_version?'Official current source':'Wayback + Common Crawl',extraction:D.method};
    return `${head('Readiness / dependencies')}<h2 class="detail-title">A staged run.<br><span class="serif">A visible outcome.</span></h2>${fields(values,S.fieldPage,'fields')}${spacer}<p class="context-note">${D.config.study_version?'Current source failures are reported in collection. OpenRouter is selected; paid inference allowances remain deferred.':'Archive blocks are reported here and in query outcomes. Codex is optional for rule extraction.'}</p>${operation('doctor','Run system checks →')}`;
  }
  if(S.detail==='paths') {
    const entries=D.config.employers.flatMap(e=>e.paths.map(p=>[e.name,p]));
    return `${head('Configured archive namespaces')}<h2 class="detail-title">Where we look.<br><span class="serif">What we preserve.</span></h2>${fields(Object.fromEntries(entries.map(([name,path],i)=>[`${name} path ${i+1}`,path])),S.fieldPage,'fields')}${spacer}<p class="context-note">Queries and their failures are retained alongside candidate capture identifiers.</p>`;
  }
  return `${head('Scope / evidence rules')}<h2 class="detail-title">Screen before<br><span class="serif">you count.</span></h2>${fields({body:D.config.study_version?'Readable text; completeness reviewed separately':'Substantive responsibilities and requirements',employer:'Employer identity in preserved body',role:D.config.study_version?'Knowledge duties; unfamiliar titles remain review candidates':'Unique enabled role track; excluded titles stay rejected',geography:'Posting-specific US location or US-remote evidence',vacancy:'Source-scoped posting identity',dates:'Publication, capture, retrieval and first observation stay independent',cohorts:'Matched employer / brand / role / subtype / level',percentages:`At least ${D.config.comparison_minimum||5} assessed distinct vacancies`,missing_evidence:'Unavailable; never proof of requirement removal'},S.fieldPage,'fields')}${spacer}${button('Inspect recovered captures →','open','documents',false,'muted-button')}`;
}
function staticRight() {
  if(S.detail==='config') return `${head('New run / saved boundaries')}${spacer}<h2 class="section-title">Define. Check.<br><span class="serif">Then begin.</span></h2>${spacer}${row('Scope','US posting evidence')}${row('Rule extraction','No model dependency')}${row('Raw sources','Local files')}${row('Existing runs','Preserved')}${spacer}<p class="context-note">Create the run, check system readiness, then start or resume the research. Extraction and retrieval budgets are enforced by the pipeline.</p>`;
  const failure=D.job.run_id===S.run&&D.job.status==='failed';
  return `${head('Pipeline / actions')}${spacer}<h2 class="section-title">${activeHere()?'Work in progress.':failure?'Action needed.':'Evidence first.'}<br><span class="serif">${activeHere()?'Checkpoints stay local.':failure?'The cause is visible.':'Limits stay explicit.'}</span></h2>${spacer}${failure?`<div class="status-note error-note">${esc(D.job.detail.slice(-650))}</div>`:''}${row('Candidates',fmt(D.counts.candidates))}${row('Eligible observations',D.counts.eligible)}${D.config.study_version?row('Collection requests',D.collection?.usage?.requests||0)+row('Request-count limit','None')+row('Download allowance','200 MB initially; cumulative extensions recorded'):row('Request budget left',D.discovery_left)+row('Body attempts left',D.body_left)}${spacer}<div class="action-buttons">${D.config.study_version?button('Knowledge study / groups and evaluation','open','study',false,'muted-button'):''}${button('Company collection / refresh','open','collection',false,'muted-button')}${operation('doctor','Check system')}${operation('report','Build reports')}${button('Activity and limits','open','activity',false,'muted-button')}</div>${activeHere()?button('Stop operation','stop','',false,'muted-button'):''}<p class="tiny">A completed operation may still leave insufficient evidence for comparison.</p>`;
}
function expanded() {
  const records=Boolean(S.kind);
  root.innerHTML=`<main class="expanded" aria-label="Expanded ${esc(current()[1])} workspace">${[
    ['tone-blue',records?recordLeft():staticLeft()],['tone-ivory',records?recordCenter():staticCenter()],['tone-cream',records?recordRight():staticRight()],
  ].map(([tone,content],i)=>`<section class="tile ${tone} ${S.tab===i?'compact-active':''}">${content}</section>`).join('')}</main>${compactTabs()}`;
}
function render() { if(D) S.detail?expanded():mosaic(); }
async function refresh(force=false) {
  const run=S.run;
  const data=await api(url('/api/state'));
  if(run!==S.run) return;
  D=data; S.run=D.run_id;
  const jobKey=JSON.stringify([D.job.status,D.job.started_at]);
  const changed=jobKey!==lastJob; lastJob=jobKey;
  if(force||(!S.detail&&!document.activeElement?.matches('input,textarea,select'))) render();
  if(changed&&S.detail&&S.kind) await loadRecords();
  else if(changed&&S.detail&&!root.contains(document.activeElement)) render();
}
function collection(detail) {
  if(detail==='selections') return 'selections';
  if(S.step==='discover'&&detail!=='paths'&&detail!=='status') return detail==='coverage'?'coverage':'queries';
  if(S.step==='recover'&&detail!=='rules'&&detail!=='status') return 'captures';
  if(S.step==='extract') return detail==='documents'?'captures':'requirements';
  if(S.step==='review') return 'requirements';
  if(S.step==='compare') return ['cohorts','combinations','wording'].includes(detail)?detail:S.compare;
  return '';
}
async function openDetail(detail) {
  S.focusTile=detail; S.detail=detail; S.tab=1; S.fieldPage=0; S.sourceFieldPage=0; S.page=0;
  S.kind=['config','collection','import','journey','exports','paths','rules'].includes(detail)?'':collection(detail);
  S.source=null; S.selected=null;
  if(S.kind) {S.page=S.recordContexts.get(recordContextKey())?.page||0;await loadRecords();} else render();
}
function recordContextKey() {return [S.run,S.kind,S.company,S.period,S.search,S.pending,S.category,S.statement_scope,S.status,S.unknown].join('|');}
function rememberRecord() {S.recordContexts.set(recordContextKey(),{page:S.page,id:S.selected?.id});if(S.selected?.document_id) S.documentId=S.selected.document_id;}
async function loadRecords() {
  const epoch=++S.epoch;
  const result=await api(url('/api/records',{kind:S.kind,page:S.page,size:4,search:S.search,employer_id:S.company,period_id:S.period,pending:S.pending?'1':'',category:S.category,statement_scope:S.statement_scope,status:S.status,unknown:S.unknown?'1':''}));
  if(epoch!==S.epoch) return;
  S.items=result.items; S.page=result.page; S.pages=result.pages; S.total=result.total;
  const remembered=S.recordContexts.get(recordContextKey())?.id;
  S.selected=S.items.find(r=>r.id===(S.selected?.id||remembered))||S.items.find(r=>r.document_id&&r.document_id===S.documentId)||S.items[0]||null;
  rememberRecord();
  await loadSource(true,epoch); if(epoch===S.epoch) render();
}
async function loadSource(anchor=false, epoch=S.epoch) {
  const r=S.selected;
  if(!r?.observation_id) { S.source=null; return; }
  const result=await api(url('/api/source',{id:r.observation_id,page:anchor?0:S.sourcePage,anchor:anchor?r.evidence||'':''}));
  if(epoch!==S.epoch||r.id!==S.selected?.id) return;
  S.source=result; S.sourcePage=result.page;
}
async function stage(id) {
  if(!stages.some(s=>s[0]===id)) return;
  S.epoch++; S.step=id; S.detail=null; S.kind=''; S.items=[]; S.page=0; S.tab=1; S.search='';S.category='';S.statement_scope='';S.status='';S.unknown=false;render();
}
async function handle(action,value) {
  if(['company-verify','verify-all','verify-resume','verify-retry','verify-stop'].includes(action)) {
    try {
      const payload=action==='company-verify'?{ids:[value]}:action==='verify-resume'?{resume:true}:action==='verify-retry'?{resume:true,retry_incomplete:true}:{};
      S.verification=await api(action==='verify-stop'?'/api/company-verifications/stop':'/api/company-verifications',payload);
      S.verificationError=null; render();
    } catch(error) {S.verificationError=error.message;render();}
    return;
  }
  if(action==='company-detail') {S.companyDetail=await api('/api/company?'+new URLSearchParams({id:value}));render();return;}
  if(action==='company-add') {const a=S.companyAdd||{};const c=await api('/api/companies',{...a,aliases:(a.aliases||'').split(',').map(v=>v.trim()).filter(Boolean)});await refresh();S.companyDetail=c;S.companyAdd={};draft().employers=[...new Set([...draft().employers,c.id])];S.companyResults=null;render();notify('Company saved. Verify its career sources before collection.');return;}

  if(action==='companies-prev'||action==='companies-next') {S.companyPage=Math.max(0,(S.companyPage||0)+(action==='companies-next'?1:-1));return searchCompanies();}
  if(action==='collection-extend') {const additions=Object.fromEntries(Object.entries(S.extensions||{}).filter(([,v])=>v));await api('/api/collection/continue',{run_id:S.run,additions});S.extensions={};await refresh(true);notify('Budget extension recorded. Continue collection or run extraction to use its allowance.');return;}
  if(action==='scope-continuation') {S.draft=null;const d=draft();d.parent_run_id=S.run;d.run_id=S.run+'-continuation';d.employers=D.config.employers.map(e=>e.id);d.periods=D.config.periods.filter(p=>!p.id.startsWith('CURRENT-')).map(p=>p.id);S.configTab='scope';return openDetail('config');}
  if(action==='stage') return stage(value);
  if(action==='prev'||action==='next') return stage(stages[Math.min(6,Math.max(0,stages.indexOf(current())+(action==='next'?1:-1)))][0]);
  if(action==='open') return openDetail(value);
  if(action==='close') { S.epoch++; S.detail=null; S.kind=''; S.tab=1;render();root.querySelector(`[data-open="${S.focusTile}"]`)?.focus();return; }
  if(action==='compact') {S.tab=Number(value);render();return;}
  if(action==='new-run') {S.companyResults=null;S.companyDetail=null;S.configTab='scope';S.draft=null;return openDetail('config');}
  if(action==='config-tab') {S.configTab=value;render();return;}
  if(action==='compare') {S.compare=value;S.page=0;return openDetail(value==='changes'?'primary':value);}
  if(action==='coverage') {const [company,year]=value.split('|');S.company=company;S.period='';S.search=year;return openDetail('coverage');}
  if(action==='select') {S.selected=S.items.find(r=>r.id===value);rememberRecord();S.fieldPage=0;S.sourceFieldPage=0;await loadSource(true);render();return;}
  if(action==='go-review') {S.step='review';S.detail='primary';S.tab=2;render();return;}
  if(action==='review-status') {reviewDraft(S.selected).status=value;render();return;}
  if(action==='import-open') return openDetail('import');
  if(action==='import-save') {
    S.busy=true;render();
    try {const postings=JSON.parse(S.importText);const result=await api('/api/import',{run_id:S.run,postings});await refresh(true);notify(`Imported ${result.imported} of ${result.provided} descriptions. ${result.status==='document_limit'?'The run document limit is reached.':'Run extraction next.'}`);}
    finally {S.busy=false;render();}return;
  }
  if(action==='approve-document') {
    const list=await api(url('/api/document-review'));
    const record=list.find(r=>r.document_id===S.selected.document_id);
    if(!record) throw new Error('Extract this document before reviewing it.');
    if(D.config.study_version&&!S.docReview?.omission_audited) throw new Error('Read the full description and record the omission audit first.');
    await api('/api/document-review',{run_id:S.run,...record,...(D.config.study_version?{...S.docReview,additions:JSON.parse(S.docReview.additions||'[]')}:{})});
    await refresh();await loadRecords();notify('Document review saved. Rebuild reports.');return;
  }
  if(action==='study-action') {const decisions=['approve-taxonomy','evaluate'].includes(value)?JSON.parse(S.studyText||'{}'):undefined;const result=await api('/api/study',{run_id:S.run,action:value,decisions});await refresh();render();notify(result.status||'Study artifact saved; download it in Report.');return;}
  if(action==='create-study') {const result=await api('/api/runs',{run_id:draft().run_id,preset:'capital-one-knowledge'});S.run=result.run_id;S.detail=null;S.kind='';S.draft=null;await refresh(true);notify('Capital One study created; model inference is gated.');return;}
  if(action==='create-run') {
    if(draft().source_ids) draft().source_ids=draft().source_ids.filter(id=>D.catalog.employers.some(e=>draft().employers.includes(e.id)&&(e.current_sources||[]).some(s=>s.id===id)));
    S.busy=true;render();
    try {const result=await api('/api/runs',draft());S.run=result.run_id;S.detail=null;S.kind='';S.step='prepare';S.company='';S.period='';S.search='';S.draft=null;await refresh(true);notify('Local run created. Scope and request limits saved.');}
    finally {S.busy=false;render();} return;
  }
  if(action==='operation') {
    S.busy=true;render();
    try {await api('/api/jobs',{run_id:S.run,operation:value,codex:D.method==='codex'});await refresh(true);notify(human(value)+' started.');}
    finally {S.busy=false;render();}return;
  }
  if(action==='stop') {await api('/api/stop',{});await refresh(true);notify('Stop requested. Saved checkpoints remain available.');return;}
  if(action==='save-review') {
    const record=S.selected, d=reviewDraft(record);S.busy=true;render();
    try {await api('/api/review',{run_id:S.run,requirement_id:record.id,status:d.status,concept:d.concept||null,statement_scope:d.statement_scope,excluded:d.excluded,note:d.note,revision:d.revision});S.reviewDrafts.delete(record.id);await refresh();await loadRecords();notify('Human approval saved. Rebuild reports to apply this decision.');}
    finally {S.busy=false;render();}return;
  }
  const match=action.match(/^(records|source|fields|provenance|period|exports)-(prev|next)$/);
  if(match) {
    const delta=match[2]==='next'?1:-1;
    if(match[1]==='records') {S.page+=delta;await loadRecords();}
    if(match[1]==='source') {S.sourcePage+=delta;await loadSource();render();}
    if(match[1]==='fields') {S.fieldPage=Math.max(0,S.fieldPage+delta);render();}
    if(match[1]==='provenance') {S.sourceFieldPage=Math.max(0,S.sourceFieldPage+delta);render();}
    if(match[1]==='period') {S.periodPage+=delta;render();}
    if(match[1]==='exports') {S.exportPage+=delta;render();}
  }
}
root.addEventListener('click',event=>{
  const target=event.target.closest('[data-action]');
  if(target) {if(!target.disabled) handle(target.dataset.action,target.dataset.value).catch(error=>notify(error.message));return;}
  const card=event.target.closest('[data-open]');
  if(card&&!event.target.closest('input,select,textarea,a,button')) openDetail(card.dataset.open).catch(error=>notify(error.message));
});
root.addEventListener('keydown',event=>{
  if(event.target.matches('[data-open]')&&['Enter',' '].includes(event.key)) {event.preventDefault();openDetail(event.target.dataset.open).catch(error=>notify(error.message));}
});
root.addEventListener('change',async event=>{
  const t=event.target;
  try {
    if(t.dataset.field==='stage') await stage(t.value);
    if(t.dataset.field==='run') {S.run=t.value;S.detail=null;S.kind='';S.company='';S.period='';S.search='';S.reviewDrafts.clear();S.epoch++;await refresh(true);}
    if(['company','period','pending','category','statement_scope','status','unknown'].includes(t.dataset.field)) {S[t.dataset.field]=t.type==='checkbox'?t.checked:t.value;S.page=0;await loadRecords();}
    if(t.dataset.field==='review-concept') reviewDraft(S.selected).concept=t.value;
    if(t.dataset.field==='review-scope') reviewDraft(S.selected).statement_scope=t.value;
    if(t.dataset.field==='review-excluded') reviewDraft(S.selected).excluded=t.checked;
    if(t.dataset.field==='import-file'&&t.files?.[0]) {if(t.files[0].size>5000000) throw new Error('Choose a file below 5 MB.'); S.importText=await t.files[0].text();render();}
    if(t.dataset.source) {const d=draft();d.source_ids??=D.catalog.employers.flatMap(e=>(e.current_sources||[]).map(s=>s.id));d.source_ids=t.checked?[...new Set([...d.source_ids,t.dataset.source])]:d.source_ids.filter(id=>id!==t.dataset.source);}
    if(t.dataset.field==='study-json') {S.studyText=t.value;return;}
    if(t.dataset.docReview) {S.docReview[t.dataset.docReview]=t.type==='checkbox'?t.checked:t.value;return;}
    if(t.dataset.draft) {draft()[t.dataset.draft]=t.type==='number'?(t.value===''?null:Number(t.value)):t.value; if(t.dataset.draft==='comparison_scope') {draft().comparison_status=t.value==='qualification'?'required_or_preferred':'stated';render();}}
    if(t.dataset.check) {const d=draft(),key=t.dataset.check;d[key]=t.checked?[...new Set([...d[key],t.value])]:d[key].filter(v=>v!==t.value);}
  }catch(error){notify(error.message);}
});
root.addEventListener('input',event=>{
  const t=event.target;
  if(t.dataset.library) {S.companyAdd??={};S.companyAdd[t.dataset.library]=t.value;}
  if(t.dataset.extension) {S.extensions??={};S.extensions[t.dataset.extension]=t.value===''?null:Number(t.value);}
  if(t.dataset.field==='company-search') {S.companyQuery=t.value;S.companyPage=0;clearTimeout(searchTimer);searchTimer=setTimeout(async()=>{const position=t.selectionStart;try{await searchCompanies();const f=document.getElementById('company-search');f?.focus();f?.setSelectionRange(position,position);}catch(error){notify(error.message);}},240);}
  if(t.dataset.field==='import-text') S.importText=t.value;
  if(t.dataset.field==='review-note'&&S.selected) reviewDraft(S.selected).note=t.value;
  if(t.dataset.field==='study-json') {S.studyText=t.value;return;}
    if(t.dataset.docReview) {S.docReview[t.dataset.docReview]=t.type==='checkbox'?t.checked:t.value;return;}
    if(t.dataset.draft) {draft()[t.dataset.draft]=t.type==='number'?(t.value===''?null:Number(t.value)):t.value; if(t.dataset.draft==='comparison_scope') {draft().comparison_status=t.value==='qualification'?'required_or_preferred':'stated';render();}}
  if(t.dataset.field==='search') {
    S.search=t.value;S.page=0;clearTimeout(searchTimer);
    searchTimer=setTimeout(async()=>{const position=t.selectionStart;try{await loadRecords();const f=document.getElementById('record-search');f?.focus();f?.setSelectionRange(position,position);}catch(error){notify(error.message);}},240);
  }
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&S.detail) handle('close').catch(error=>notify(error.message));
  if(event.altKey&&['ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault();handle(event.key==='ArrowLeft'?'prev':'next').catch(error=>notify(error.message));}
});
async function boot() {
  try {D=await api('/api/state');S.run=D.run_id;if(!D.exists) {S.detail='config';draft();}render();}
  catch(error) {root.innerHTML=`<div class="boot">degent.<small>${esc(error.message)}</small>${button('Reconnect','reconnect','',false,'muted-button')}</div>`;}
}
root.addEventListener('click',event=>{if(event.target.dataset.action==='reconnect') boot();});
boot();
setInterval(()=>{if(D&&!S.busy) pollVerification().then(()=>refresh()).catch(()=>notify('Local agent connection interrupted. Reconnecting…'));},2500);
