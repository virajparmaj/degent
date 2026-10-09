// Runs the UI state/render functions in Node, without a browser or live archive requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const base = path.resolve(__dirname, '..');
const state = {
  run_id:'unit-run',runs:['unit-run'],token:'unit-test-token',exists:true,
  config:{employers:[{id:'stripe',name:'Stripe',paths:['stripe.com/jobs/*']}],
    periods:[{id:'2025-Q1',start:'2025-01-01',end:'2025-03-31',kind:'completed'}],
    roles:{tracks:['credit_risk_analytics']},discovery_request_cap:300,body_attempt_cap:150,
    extract_document_cap:30,semantic_review_call_cap:10,codex_model:'configured-model'},
  counts:{candidates:7,queries:1,blocked:0,attempts:2,retrieved:1,eligible:1,rejected:0,failed:1,
    vacancies:1,documents:1,requirements:2,pending:2,approved:0,changes:0,cohorts:0},
  method:'rules',job:{},doctor:null,coverage:[],discovery_used:2,discovery_left:298,body_left:148,
  preview_queries:[],preview_requirements:[],preview_observations:[],report:{},report_stale:false,exports:[],
};
state.catalog=structuredClone(state.config);
state.catalog.employers.push({id:'capital_one',name:'Capital One',paths:['www.capitalonecareers.com/job/*']});
const records=[{id:'requirement-a',observation_id:'obs-a',employer:'Stripe',employer_id:'stripe',
  concept:'sql',status:'required',phrase:'SQL required.',evidence:'SQL',human_review:'pending',revision:0},
  {id:'requirement-b',observation_id:'obs-b',employer:'Stripe',employer_id:'stripe',
  concept:'python',status:'unclear',phrase:'Python familiarity.',evidence:'Python',human_review:'pending',revision:0}];
const elements={app:{innerHTML:'',addEventListener(){},querySelector(){return null;},contains(){return false;}},
  notice:{textContent:'',classList:{add(){},remove(){}}}};
const calls=[];
const context = vm.createContext({
  URLSearchParams,console,structuredClone,setTimeout:()=>0,clearTimeout(){},setInterval(){},
  document:{getElementById:id=>elements[id]||null,addEventListener(){},activeElement:{matches:()=>false}},
  fetch:async (address,options)=>{
    calls.push({address,options});
    const url = new URL(address,'http://unit.test');
    let result;
    if(url.pathname==='/api/state') result=structuredClone(state);
    else if(url.pathname==='/api/records') result={items:structuredClone(records),page:0,pages:1,total:2};
    else if(url.pathname==='/api/source') result={text:'SQL required. Python familiarity.',page:0,pages:1,verified:true};
    else result={};
    return {ok:true,json:async()=>result};
  },
});
const evaluate = code=>vm.runInContext(code,context);
vm.runInContext(fs.readFileSync(path.join(base,'degent/web/app.js'),'utf8'),context);

(async()=>{
  await new Promise(setImmediate);
  for(const stage of ['prepare','discover','recover','extract','review','compare','report']) {
    await evaluate(`stage('${stage}')`);
    const html=elements.app.innerHTML;
    const count=(html.match(/<section class="tile /g)||[]).length;
    assert.equal(count,['prepare','extract','review','report'].includes(stage)?6:7,stage+' tile count');
    assert.ok(html.includes('class="mosaic"'),stage+' is a mosaic');
    assert.equal((html.match(/class="stack /g)||[]).length,3,stage+' has 3 columns');
    assert.ok(!html.includes('NaN')&&!html.includes('undefined'),stage+' has defined content');
  }
  await evaluate("stage('prepare'); openDetail('config')");
  assert.ok(elements.app.innerHTML.includes('Create local run'));
  assert.equal(evaluate('draft().employers.length'),2);
  assert.ok(elements.app.innerHTML.includes('Capital One'));
  evaluate("S.configTab='budgets';render()");
  assert.ok(elements.app.innerHTML.includes('current_document_cap'));
  assert.ok(elements.app.innerHTML.includes('current_request_cap'));
  assert.equal(evaluate('draft().current_document_cap'),null);
  evaluate("draft().current_document_cap=10;S.configTab='sources';render()");
  assert.equal(evaluate('draft().current_document_cap'),10);
  assert.ok(elements.app.innerHTML.includes('Senior Associate'));
  evaluate("draft().employers=[];S.configTab='budgets';render()");
  assert.equal(evaluate('draft().employers.length'),0,'scope is retained across config sections');
  evaluate("S.companyDetail={id:'stripe',name:'Stripe',website:'https://stripe.com',source_status:'unverified',current_sources:[{id:'board1',provider:'greenhouse',url:'https://boards.greenhouse.io/stripe',status:'unverified',provenance:'<script>untrusted</script>'}]};S.configTab='scope';render()");
  assert.ok(elements.app.innerHTML.includes('Search name, alias or website domain'));
  assert.ok(elements.app.innerHTML.includes('Add company or another career source'));
  assert.ok(elements.app.innerHTML.includes('Verify / discover career sources'));
  assert.ok(!elements.app.innerHTML.includes('<script>untrusted'));
  evaluate("S.companyDetail.verification_history=[{attempt_id:'old',at:'2026-01-01',sources:[{provider:'dom',status:'verified',verification_outcome:'<script>history</script>'}]}];S.companyDetail.verification_evidence=[{url:'<img src=x>'}];render()");
  assert.ok(elements.app.innerHTML.includes('prior successful attempts: 1'));
  assert.ok(elements.app.innerHTML.includes('&lt;script&gt;history&lt;/script&gt;'));
  assert.ok(!elements.app.innerHTML.includes('<img src=x>'));
  evaluate("S.verification={status:'running',processed:12,total:84,remaining:72,counts:{verified:5},directory:'/local/report',results:[{name:'<script>bad</script>',reason:'<img src=x>',remaining_action:'Retry'}]};render()");
  assert.ok(elements.app.innerHTML.includes('12 / 84 processed'));
  assert.ok(elements.app.innerHTML.includes('Stop verification'));
  assert.ok(elements.app.innerHTML.includes('Deferred: 0'));
  assert.ok(elements.app.innerHTML.includes('Active time:'));
  assert.ok(elements.app.innerHTML.includes('Requests:'));
  assert.ok(elements.app.innerHTML.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!elements.app.innerHTML.includes('<img src=x>'));
  evaluate("S.verificationError='<script>error</script>';S.verification.status='stopped';render()");
  assert.ok(elements.app.innerHTML.includes('Resume incomplete batch'));
  assert.ok(elements.app.innerHTML.includes('&lt;script&gt;error&lt;/script&gt;'));
  evaluate("S.verification=null;S.verificationError=null");
  evaluate("D.config.collection_version=2;D.collection={id:'session',status:'paused',pause_reason:'request_limit',document_limit:null,counts:{discovered:73,retrieved:25,pending_extraction:25},sources:{a:{company_id:'stripe',source:{provider:'greenhouse'},status:'processing',discovered:73,retrieved:25,eligible:25,inventory_complete:true}},usage:{requests:1,bytes:5000,seconds:1,storage:1000},budgets:{requests:1,bytes:10000,seconds:120,storage:10000}};");
  await evaluate("stage('recover');openDetail('collection')");
  assert.ok(elements.app.innerHTML.includes('Continue collection'));
  assert.ok(elements.app.innerHTML.includes('Optional document limit: none'));
  assert.ok(elements.app.innerHTML.includes('Record budget extension'));
  evaluate("S.extensions={requests:5}");
  await evaluate("handle('collection-extend','')");
  const extension=calls.find(c=>c.address==='/api/collection/continue');
  assert.deepEqual(JSON.parse(extension.options.body).additions,{requests:5});
  evaluate("D.config.collection_version=null;D.collection=null;S.companyDetail=null;");
  await evaluate("stage('review');openDetail('queue')");
  assert.equal(evaluate('S.selected.id'),'requirement-a');
  await evaluate("handle('review-status','preferred')");
  evaluate("reviewDraft(S.selected).note='review A'");
  await evaluate("handle('select','requirement-b')");
  assert.equal(evaluate('reviewDraft(S.selected).status'),'unclear','review state does not leak to next record');
  await evaluate("handle('select','requirement-a')");
  assert.equal(evaluate('reviewDraft(S.selected).status'),'preferred','draft status survives record switching');
  assert.equal(evaluate('reviewDraft(S.selected).note'),'review A');
  await evaluate("stage('recover');openDetail('primary')");
  assert.ok(calls.some(call=>call.address.includes('kind=captures')));
  await evaluate("stage('discover');handle('coverage','stripe|2025')");
  assert.ok(calls.some(call=>call.address.includes('kind=coverage')&&call.address.includes('employer_id=stripe')));
  const escaped=evaluate("fields({evidence:'<script>bad</script>'},0,'fields')");
  assert.ok(!escaped.includes('<script>'));
  assert.ok(escaped.includes('&lt;script&gt;'));
  evaluate("S.longValue='x'.repeat(450)");
  const first=evaluate("fields({evidence:S.longValue},0,'fields')");
  assert.ok(first.includes('Next →'),'long provenance has additional pages');
  assert.ok(evaluate("fields({evidence:S.longValue},1,'fields')").includes('evidence (5/5)'));
  evaluate('D.counts.eligible=0;D.body_left=0;D.discovery_left=0');
  for(const name of ['fetch','extract','discover']) assert.ok(evaluate(`operation('${name}','Run')`).includes('disabled'));
  evaluate("D.config.sources=['current'];D.config.current_document_cap=10;D.config.current_request_cap=24;D.counts.current_saved=10;D.current_requests_used=14");
  await evaluate("stage('recover')");
  assert.ok(elements.app.innerHTML.includes('Saved document limit · 0 left'));
  assert.ok(elements.app.innerHTML.includes('Requests including retries: 14 / 24'));
  assert.ok(!elements.app.innerHTML.includes('Archive retrieval cap'));
  evaluate("D.config.study_version=1;D.config.comparison_minimum=30;D.method='provider_pending';D.config.periods.unshift({id:'UNKNOWN',start:null,end:null,kind:'unknown'});D.collection={counts:{discovered:42,retrieved:20},usage:{requests:23},sources:{}};");
  for(const name of ['prepare','discover','recover','extract','review','compare','report']) {
    await evaluate(`stage('${name}')`);
    assert.ok(!elements.app.innerHTML.includes('n ≥ 5'));
    assert.ok(!elements.app.innerHTML.includes('Start / resume discovery'));
    assert.ok(!elements.app.innerHTML.includes('Start / resume recovery'));
  }
  assert.ok(evaluate('staticRight()').includes('Request-count limit'));
  assert.ok(evaluate('studyPanel()').includes('Freeze 60-document gold packet'));
  assert.ok(evaluate('statusTile()').includes('locally.'));
  evaluate("D.method='openrouter';D.config.inference={identity:{model:'openai/gpt-6-luna'},enabled:false};");
  await evaluate("stage('extract')");
  assert.ok(elements.app.innerHTML.includes('OpenRouter.'));
  assert.ok(elements.app.innerHTML.includes('openai/gpt-6-luna'));
  assert.ok(elements.app.innerHTML.includes('paid allowance required'));
  const css=fs.readFileSync(path.join(base,'degent/web/styles.css'),'utf8');
  assert.ok(css.startsWith(':root'));
  assert.ok(css.includes('.stack.center.evidence-stack{grid-template-rows:minmax(0,70fr) minmax(0,30fr)}'));
  assert.ok(css.includes('height:100dvh'));
  assert.ok(css.includes('overflow:hidden'));
  assert.ok(!css.includes('fonts.googleapis'));
  console.log('Frontend state/render checks passed: seven mosaics, expanded records, filters, drafts, escaping, pagination and operation guards.');
})().catch(error=>{console.error(error);process.exitCode=1;});
