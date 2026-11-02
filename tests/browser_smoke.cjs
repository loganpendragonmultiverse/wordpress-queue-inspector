const {chromium}=require('playwright');const fs=require('fs');const cp=require('child_process');const os=require('os');const path=require('path');
(async()=>{
 const root=process.cwd();const x=JSON.parse(fs.readFileSync('samples/baseline.json','utf8'));
 x.jobs=Array.from({length:301},(_,i)=>({kind:'action',id:String(i),hook:'bulk_hook',group:'batch',status:'pending',scheduled:'2026-11-01T12:00:00Z'}));x.logs=[];x.coverage.action.source_count=301;x.coverage.cron.source_count=0;x.coverage.log.source_count=0;
 const file=path.join(os.tmpdir(),'wqi-browser-fixture.json');fs.writeFileSync(file,JSON.stringify(x));const port=19787;
 const server=cp.spawn('python3',['-m','wqi','serve',file,'--port',String(port)],{cwd:root});
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();
  for(let i=0;i<50;i++){try{await page.goto(`http://127.0.0.1:${port}/`);break}catch{await new Promise(r=>setTimeout(r,100))}}
  await page.locator('input.job-select[value="action:0"]').check();
  await page.goto(`http://127.0.0.1:${port}/?page=4`);
  await page.locator('input.job-select[value="action:300"]').check();
  await page.goto(`http://127.0.0.1:${port}/?q=bulk_hook&page=1`);
  if(!(await page.locator('input.job-select[value="action:0"]').isChecked()))throw Error('first selection lost');
  if(!(await page.locator('#selection-count').innerText()).includes('2 jobs selected'))throw Error('selection count wrong');
  await page.goto(`http://127.0.0.1:${port}/?page=4`);
  if(!(await page.locator('input.job-select[value="action:300"]').isChecked()))throw Error('page four selection lost');
  await page.goto(`http://127.0.0.1:${port}/?state=complete&min_age=2&group_by=status`);
  if(!(await page.locator('body').innerText()).includes('0 matching jobs'))throw Error('state and age filter failed');
  if(!(await page.locator('#selection-count').innerText()).includes('2 jobs selected'))throw Error('selection lost across empty filter');
  await page.goto(`http://127.0.0.1:${port}/?state=pending&min_age=1&group_by=status&page=4`);
  if(!(await page.locator('input.job-select[value="action:300"]').isChecked()))throw Error('selection lost across state and age filter');
  if(!(await page.locator('body').innerText()).includes('Grouped by status'))throw Error('state grouping absent');
  console.log('PASS Chromium selection across pages and filters');
  const historyPath=path.join(os.tmpdir(),`wqi-browser-incident-${process.pid}`);fs.cpSync(path.join(root,'samples/incident'),historyPath,{recursive:true});
  const historyServer=cp.spawn('python3',['-m','wqi','serve',historyPath,'--port','19789'],{cwd:root});
  try {
    for(let i=0;i<50;i++){try{await page.goto('http://127.0.0.1:19789/?capture=2');break}catch{await new Promise(r=>setTimeout(r,100))}}
    if(!(await page.locator('body').innerText()).includes('Viewing capture'))throw Error('capture picker absent');
    if((await page.locator('#capture option').count())!==3)throw Error('history options incomplete');
    await page.goto('http://127.0.0.1:19789/report?baseline=1&followup=2');
    if(!(await page.locator('pre').innerText()).includes('failed → complete'))throw Error('middle to latest comparison wrong');
    await page.goto('http://127.0.0.1:19789/?capture=2');
    await page.locator('#notes').fill('Operator verified fixture');await page.getByRole('button',{name:'Save notes'}).click();
    await page.goto('http://127.0.0.1:19789/report?baseline=1&followup=2');
    if(!(await page.locator('pre').innerText()).includes('Operator verified fixture'))throw Error('saved note missing from report');
    const jsonReport=await page.request.get('http://127.0.0.1:19789/report?baseline=1&followup=2&format=json');
    if(jsonReport.status()!==200 || !(await jsonReport.json()).comparison)throw Error('JSON report failed');
    const markdownReport=await page.request.get('http://127.0.0.1:19789/report?baseline=1&followup=2&format=markdown');
    if(markdownReport.status()!==200 || !(markdownReport.headers()['content-disposition']||'').includes('wqi-report.md'))throw Error('Markdown download failed');
    const bad=await page.goto('http://127.0.0.1:19789/report?baseline=2&followup=1');
    if(bad.status()!==400)throw Error('reverse baseline accepted');
    console.log('PASS Chromium three-capture selection and chronological comparison');
  } finally {historyServer.kill();fs.rmSync(historyPath,{recursive:true,force:true})}

 }finally{await browser.close();server.kill();fs.rmSync(file,{force:true})}
})();
