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
  console.log('PASS Chromium selection across pages and filters');
 }finally{await browser.close();server.kill();fs.rmSync(file,{force:true})}
})();
