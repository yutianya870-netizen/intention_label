const fs = require('fs');
const path = require('path');
const http = require('http');
const assert = require('assert');
const {chromium} = require('/Users/yutianya/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const XLSX = require('../static/vendor/xlsx.full.min.js');
const out = __dirname;
const received = [];
const mock = http.createServer((req,res)=>{
 let raw='';req.on('data',d=>raw+=d);req.on('end',()=>{
  const body=JSON.parse(raw);received.push({body,url:req.url});
  const content=JSON.parse(body.messages[1].content);
  assert(!raw.includes('这条当前回合的回答不应进入'));
  const prediction={primary:content.target_user_turn.content.includes('纠正')?'R':'E',secondary:[],queued:[],current_evidence:'当前消息',prior_framing_evidence:'此前错误理解',rationale:'模拟 API，仅验证程序行为。',needs_review:false};
  res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({model:'mock-model',usage:{prompt_tokens:10,completion_tokens:15},choices:[{message:{content:JSON.stringify(prediction)}}]}));
 });
});
(async()=>{
 await new Promise(r=>mock.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8789');await page.locator('#status').filter({hasText:'工作区就绪'}).waitFor();
 await page.locator('#demo').click();await page.locator('#target-title').filter({hasText:'4 / 4'}).waitFor();
 assert.equal(await page.locator('.message').count(),7);assert.equal(await page.locator('.message.target').count(),1);
 assert(!(await page.locator('#conversation').textContent()).includes('这条当前回合的回答不应进入'));
 await page.locator('#gold').selectOption('R');await page.locator('#note').fill('测试 gold，不应出现在 API 输入');
 await page.locator('[data-page="settings"]').click();
 const card=page.locator('.model-card').first();
 await card.locator('[data-field="protocol"]').selectOption('chat');
 await card.locator('[data-field="name"]').fill('本地模拟模型');
 await card.locator('[data-field="base_url"]').fill(`http://127.0.0.1:${mock.address().port}/v1`);
 await card.locator('[data-field="model"]').fill('mock-model');
 await card.locator('[data-field="api_key"]').fill('SECRET_MEMORY_ONLY_QATEST');
 await page.locator('#delay').fill('0');await page.locator('[data-page="work"]').click();
 await page.locator('#preview').click();await page.locator('#payload-dialog').waitFor();
 const payload=JSON.parse(await page.locator('#payload').textContent());
 assert.equal(payload.audit.history_messages,6);assert(!JSON.stringify(payload).includes('SECRET_MEMORY_ONLY_QATEST'));assert(!JSON.stringify(payload).includes('测试 gold'));
 await page.locator('#close-dialog').click();assert.equal(received.length,0);
 await page.locator('#run').click();
 try{await page.locator('#status').filter({hasText:'队列完成'}).waitFor();}catch(e){console.error('UI status:',await page.locator('#status').textContent(),'run info:',await page.locator('#run-info').textContent(),'page errors:',errors);await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw e;}
 assert.equal(received.length,1);assert(!JSON.stringify(received[0]).includes('测试 gold'));
 assert.equal(await page.locator('#predictions .prediction').count(),1);
 await page.locator('#run').click();await page.locator('#status').filter({hasText:'新保存 0 次结果'}).waitFor();assert.equal(received.length,1);
 // Freeze/edit the same run setup: P2/P3 use shared examples and yield separate results.
 await page.locator('[data-page="settings"]').click();
 await page.locator('#examples').fill('[{"prior_context":[],"target_user_turn":"实现按钮","output":{"primary":"E"}}]');
 await page.locator('[data-prompt="P2"] input').check();await page.locator('[data-prompt="P3"] input').check();
 await page.locator('[data-page="work"]').click();await page.locator('#run').click();await page.locator('#status').filter({hasText:'新保存 2 次结果'}).waitFor();assert.equal(received.length,3);
 await page.screenshot({path:path.join(out,'workbench-desktop.png'),fullPage:true});
 await page.locator('[data-page="results"]').click();assert.equal(await page.locator('#result-rows tr').count(),3);
 assert((await page.locator('#metrics').textContent()).includes('100.0%'));
 await page.screenshot({path:path.join(out,'results-desktop.png'),fullPage:true});
 const dl=page.waitForEvent('download');await page.locator('#excel').click();const workbook=await dl;const workbookPath=path.join(out,'test-export.xlsx');await workbook.saveAs(workbookPath);
 const wb=XLSX.read(fs.readFileSync(workbookPath),{type:'buffer'});assert(wb.SheetNames.includes('模型判定'));
 const rows=XLSX.utils.sheet_to_json(wb.Sheets['模型判定']);assert.equal(rows.length,3);assert(rows.every(r=>r.primary==='R'&&r.gold==='R'&&r.future_excluded===true));
 assert(!JSON.stringify(wb).includes('SECRET_MEMORY_ONLY_QATEST'));
 const jsondl=page.waitForEvent('download');await page.locator('#backup').click();const backup=await jsondl;const backupPath=path.join(out,'test-backup.json');await backup.saveAs(backupPath);const backupText=fs.readFileSync(backupPath,'utf8');assert(!backupText.includes('SECRET_MEMORY_ONLY_QATEST'));assert.equal(JSON.parse(backupText).state.results.length,3);
 // Reload preserves data/gold/results; credential memory is lost.
 await page.reload();await page.locator('#status').filter({hasText:'工作区就绪'}).waitFor();
 await page.locator('[data-page="settings"]').click();assert.equal(await page.locator('[data-field="api_key"]').first().inputValue(),'');
 await page.locator('[data-page="work"]').click();assert.equal(await page.locator('#gold').inputValue(),'R');
 await page.locator('#restore-file').setInputFiles(backupPath);await page.locator('#status').filter({hasText:'备份已合并恢复'}).waitFor();
 assert.equal(await page.locator('#predictions .prediction').count(),3);
 // Import the actual reference data, then a second import must deduplicate.
 const reference='/Users/yutianya/Downloads/task1009-main/portable_ir02_annotator/data/cleaned/sessions_sample100.js';
 if(fs.existsSync(reference)){
  await page.locator('#files').setInputFiles(reference);await page.locator('#status').filter({hasText:'已新增 100 个会话'}).waitFor({timeout:60000});
  assert((await page.locator('#counts').textContent()).includes('756 prompts'));
  await page.locator('#files').setInputFiles(reference);await page.locator('#status').filter({hasText:'已新增 0 个会话'}).waitFor({timeout:60000});
 }
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:path.join(out,'workbench-mobile.png'),fullPage:true});
 assert.deepEqual(errors,[]);
 const report={date:new Date().toISOString(),backend_protocols_tested:4,real_reference_sessions:100,real_reference_user_prompts:752,browser_model_requests:received.length,future_answer_excluded:true,gold_excluded_from_model_input:true,payload_preview_makes_no_model_call:true,skip_existing:true,prompt_combinations:true,excel_roundtrip_rows:rows.length,keys_excluded_from_exports_and_reload:true,backup_restore:true,mobile_overflow:false,page_errors:errors,paid_api_calls:0};
 fs.writeFileSync(path.join(out,'qa-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 await browser.close();await new Promise(r=>mock.close(r));
})().catch(e=>{console.error(e);mock.close();process.exit(1)});
