import copy
import json
import tempfile
import unittest
from pathlib import Path
from wqi.core import compare, findings, load_incident, read_snapshot, report, save_incident, select_jobs, validate

BASE = {
 'schema':'wqi.snapshot/1','site':'shop-example','captured_start':'2026-11-01T12:00:00Z','captured_end':'2026-11-01T12:01:00Z',
 'source':{'wordpress':'6.8','action_scheduler':'3.9','disable_wp_cron':True},
 'jobs':[
  {'kind':'action','id':'1','hook':'send_mail','group':'1','status':'pending','scheduled':'2026-11-01T08:00:00Z'},
  {'kind':'action','id':'2','hook':'send_mail','group':'1','status':'failed','scheduled':'2026-11-01T09:00:00Z'},
  {'kind':'action','id':'3','hook':'send_mail','group':'1','status':'failed','scheduled':'2026-11-01T09:10:00Z'},
  {'kind':'cron','id':'c1','hook':'cleanup','group':'','status':'scheduled','scheduled':'2026-11-01T11:00:00Z','recurrence':'hourly','callback_present':True}],
 'logs':[{'action_id':'2','message':'Log event (message omitted for privacy)','time':'2026-11-01T09:11:00Z'}],
 'coverage':{'action':{'complete':True,'source_count':3},'cron':{'complete':True,'source_count':1},'log':{'complete':True,'source_count':1}}
}

class CoreTests(unittest.TestCase):
 def test_findings_and_runner_uncertainty(self):
  rules=[x['rule'] for x in findings(BASE)]
  self.assertIn('overdue',rules);self.assertIn('repeated-failures',rules)
  self.assertNotIn('disable_wp_cron',rules)
 def test_shared_age_state_and_group_filter(self):
  selected=select_jobs(BASE,term='send_mail',state='failed',min_age=60,group_by='status')
  self.assertEqual(len(selected['jobs']),2)
  self.assertEqual(selected['groups'],[('failed',2)])
  future=copy.deepcopy(BASE);future['jobs'][0]['scheduled']='2027-01-01T00:00:00Z'
  self.assertEqual(len(select_jobs(future)['jobs']),len(future['jobs']))
  self.assertNotIn('1',[j['id'] for j in select_jobs(future,min_age=1)['jobs']])
 def test_partial_capture_and_timezone_offset(self):
  x=copy.deepcopy(BASE);x['coverage']['action']={'complete':False,'source_count':None};x['jobs']=x['jobs'][3:];x['coverage']['log']={'complete':False,'source_count':None};x['logs']=[]
  x['captured_start']='2026-11-01T14:00:00+02:00';x['captured_end']='2026-11-01T14:01:00+02:00'
  self.assertIn('partial-capture',[r['rule'] for r in findings(x)])
  with self.assertRaises(ValueError):validate(dict(x,schema='unknown'))
 def test_partial_does_not_prove_disappearance(self):
  later=copy.deepcopy(BASE);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z'
  later['jobs']=later['jobs'][1:];later['coverage']['action']={'complete':False,'source_count':3}
  self.assertIn('unknown-partial-capture',[x['change'] for x in compare(BASE,later)['rows']])
 def test_recorded_completion(self):
  later=copy.deepcopy(BASE);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z';later['jobs'][0]['status']='complete'
  self.assertIn('progressed',[x['change'] for x in compare(BASE,later)['rows']])
  self.assertIn('followup_findings',json.loads(report(BASE,later,format='json')))
  self.assertIn('## Follow-up findings',report(BASE,later))
 def test_old_and_changed_claim_stays_evidence_only(self):
  old=copy.deepcopy(BASE);old['jobs'][0].update(status='in-progress',claim_id='4',claim_created='2026-11-01T09:00:00Z')
  self.assertIn('old-claim',[x['rule'] for x in findings(old)])
  later=copy.deepcopy(old);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z';later['jobs'][0]['claim_id']='5'
  self.assertEqual([x for x in compare(old,later)['rows'] if x['identity']=="('action', '1')"][0]['change'],'claim-changed')
 def test_ambiguous_cron(self):
  x=copy.deepcopy(BASE);x['jobs'].append(dict(x['jobs'][-1],id='c2'));x['coverage']['cron']['source_count']=2
  later=copy.deepcopy(x);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z'
  self.assertIn('ambiguous',[r['change'] for r in compare(x,later)['rows']])
 def test_unkeyed_recurring_match_is_uncertain_and_one_shot_is_distinct(self):
  old=copy.deepcopy(BASE);new=copy.deepcopy(BASE)
  new['captured_start']='2026-11-01T13:00:00Z';new['captured_end']='2026-11-01T13:01:00Z'
  self.assertIn('uncertain-match',[r['change'] for r in compare(old,new)['rows']])
  old['jobs'][-1]['recurrence']='';new['jobs'][-1]['recurrence']=''
  new['jobs'][-1]['scheduled']='2026-11-01T12:00:00Z'
  changes=[r['change'] for r in compare(old,new)['rows']]
  self.assertIn('new',changes);self.assertIn('no-longer-observed',changes)
 def test_one_shot_timezone_equivalence(self):
  old=copy.deepcopy(BASE);new=copy.deepcopy(BASE)
  old['jobs'][-1]['recurrence']='';old['jobs'][-1]['arg_fingerprint']='a'*64
  new['captured_start']='2026-11-01T13:00:00Z';new['captured_end']='2026-11-01T13:01:00Z'
  new['jobs'][-1]['recurrence']='';new['jobs'][-1]['arg_fingerprint']='a'*64
  new['jobs'][-1]['scheduled']='2026-11-01T06:00:00-05:00'
  self.assertIn('unchanged',[r['change'] for r in compare(old,new)['rows']])
 def test_reject_mixed_site_duplicate_timezone_and_count(self):
  for mutation in ('site','duplicate','timezone','count'):
   x=copy.deepcopy(BASE)
   if mutation=='site':
    y=copy.deepcopy(x);y['site']='other';y['captured_start']='2026-11-01T13:00:00Z';y['captured_end']='2026-11-01T13:01:00Z'
    with self.assertRaises(ValueError):compare(x,y)
    continue
   if mutation=='duplicate':x['jobs'].append(x['jobs'][0])
   if mutation=='timezone':x['captured_end']='2026-11-01T12:01:00'
   if mutation=='count':x['coverage']['action']['source_count']=5
   with self.assertRaises(ValueError):validate(x)
 def test_reject_unredacted_and_undeclared_import_data(self):
  from wqi.core import backup_incident
  for kind in ('arguments','claim','log_message','source_secret','coverage_secret'):
   x=copy.deepcopy(BASE)
   if kind in ('arguments','claim'):x['jobs'][0][kind]={'customer_email':'qa-secret@example.invalid'}
   elif kind=='log_message':x['logs'][0]['message']='qa-secret@example.invalid'
   elif kind=='coverage_secret':x['coverage']['action']['arguments']='qa-secret@example.invalid'
   else:x['source']['admin_email']='qa-secret@example.invalid'
   with self.assertRaises(ValueError):validate(x)
   with tempfile.TemporaryDirectory() as root:
    source=Path(root)/'source.json';source.write_text(json.dumps(x));incident=Path(root)/'incident'
    with self.assertRaises(ValueError):save_incident(incident,source)
    self.assertFalse(incident.exists())
    with self.assertRaises(OSError):backup_incident(incident,Path(root)/'backup.zip')
 def test_html_escaped_and_incident_hashes(self):
  x=copy.deepcopy(BASE);x['jobs'][0]['hook']='<script>alert(1)</script>'
  self.assertNotIn('<script>',report(x,notes='<script>',format='html'))
  markdown=report(x,notes='<script>\n# false heading',format='markdown')
  self.assertNotIn('<script>',markdown)
  self.assertNotIn('\n# false heading',markdown)
  with tempfile.TemporaryDirectory() as root:
   source=Path(root)/'source.json';source.write_text(json.dumps(x));dest=Path(root)/'incident'
   save_incident(dest,source,notes='observed');self.assertEqual(load_incident(dest)[2],'observed')
   (dest/'baseline.json').write_text('{}')
   with self.assertRaises(ValueError):load_incident(dest)
 def test_interrupted_import_has_no_committed_incident(self):
  from unittest.mock import patch
  x=copy.deepcopy(BASE)
  with tempfile.TemporaryDirectory() as root:
   source=Path(root)/'source.json';source.write_text(json.dumps(x));dest=Path(root)/'incident'
   with patch('wqi.core.shutil.copyfile',side_effect=OSError('interrupted')):
    with self.assertRaises(OSError):save_incident(dest,source)
   self.assertFalse(dest.exists())
   self.assertEqual(list(Path(root).glob('.wqi-*')),[])
 def test_large_page_source(self):
  x=copy.deepcopy(BASE);x['jobs']=[{'kind':'action','id':str(i),'hook':'bulk','group':'','status':'pending','scheduled':'2026-11-01T12:00:00Z'} for i in range(300)]
  x['logs']=[];x['coverage']['action']['source_count']=300;x['coverage']['cron']['source_count']=0;x['coverage']['log']['source_count']=0
  self.assertEqual(len(validate(x)['jobs']),300)

if __name__=='__main__':unittest.main()

class BrowserTests(unittest.TestCase):
 def test_pagination_search_logs_and_host_guard(self):
  import socket, subprocess, sys, time, urllib.request, urllib.error
  x=copy.deepcopy(BASE)
  x['jobs']=[{'kind':'action','id':str(i),'hook':'bulk_hook','group':'batch','status':'pending','scheduled':'2026-11-01T12:00:00Z'} for i in range(301)]
  x['logs']=[{'action_id':'300','message':'Log event (message omitted for privacy)','time':'2026-11-01T12:00:00Z'},{'action_id':'299','message':'Log event (message omitted for privacy)','time':'2026-11-01T12:30:00+01:00'}]
  x['coverage']['action']['source_count']=301;x['coverage']['cron']['source_count']=0
  x['coverage']['log']['source_count']=2
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'input.json';p.write_text(json.dumps(x))
   with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
   proc=subprocess.Popen([sys.executable,'-m','wqi','serve',str(p),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,universal_newlines=True)
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   try:
    response=None;last_error=None
    for _ in range(100):
     try:
      response=opener.open(f'http://127.0.0.1:{port}/?page=4',timeout=.5);break
     except Exception as error:
      last_error=error
      if proc.poll() is not None:break
      time.sleep(.1)
    if response is None: raise AssertionError(f'local server failed: {last_error}; process exit {proc.poll()}')
    body=response.read().decode();response.close();self.assertIn('>300</td>',body);self.assertNotIn('>299</td>',body)
    with opener.open(f'http://127.0.0.1:{port}/logs') as log_response:
     log_body=log_response.read().decode();self.assertIn('Action log timeline',log_body)
     self.assertLess(log_body.index('>299</td>'),log_body.index('>300</td>'))
    with opener.open(f'http://127.0.0.1:{port}/?state=pending&min_age=0&group_by=status') as filtered:
     self.assertIn('Grouped by status',filtered.read().decode())
    request=urllib.request.Request(f'http://127.0.0.1:{port}/',headers={'Host':'evil.example'})
    with self.assertRaises(urllib.error.HTTPError) as ctx:opener.open(request)
    self.assertEqual(ctx.exception.code,403);ctx.exception.close()
   finally:proc.terminate();proc.wait(timeout=3);proc.stderr.close()

class HistoryTests(unittest.TestCase):
 def test_old_middle_latest_and_backup_restore(self):
  from wqi.core import add_capture,backup_incident,restore_incident,load_history
  with tempfile.TemporaryDirectory() as root:
   paths=[]
   for i,status in enumerate(('pending','failed','complete')):
    x=copy.deepcopy(BASE)
    x['captured_start']=f'2026-11-01T{12+i:02d}:00:00Z';x['captured_end']=f'2026-11-01T{12+i:02d}:01:00Z'
    x['jobs'][0]['status']=status
    p=Path(root)/f'{i}.json';p.write_text(json.dumps(x));paths.append(p)
   incident=Path(root)/'incident';save_incident(incident,paths[0],paths[1]);add_capture(incident,paths[2])
   captures,notes=load_history(incident)
   self.assertEqual(len(captures),3)
   self.assertEqual(compare(captures[0],captures[2])['rows'][0]['change'] in ('progressed','unchanged','changed'),True)
   self.assertEqual([r for r in compare(captures[1],captures[2])['rows'] if r['identity']=="('action', '1')"][0]['change'],'progressed')
   archive=Path(root)/'backup.zip';backup_incident(incident,archive)
   restored=Path(root)/'restored';restore_incident(archive,restored)
   self.assertEqual(len(load_history(restored)[0]),3)
   self.assertEqual((incident/'incident.json').read_bytes(),(restored/'incident.json').read_bytes())
   with self.assertRaises(ValueError):add_capture(incident,paths[0])
 def test_notes_update_and_interrupted_write(self):
  from unittest.mock import patch
  from wqi.core import update_notes
  with tempfile.TemporaryDirectory() as root:
   source=Path(root)/'source.json';source.write_text(json.dumps(BASE));incident=Path(root)/'incident';save_incident(incident,source,notes='first')
   update_notes(incident,'second',append=True);self.assertEqual(load_incident(incident)[2],'first\nsecond')
   with patch('wqi.core.os.replace',side_effect=OSError('interrupted')):
    with self.assertRaises(OSError):update_notes(incident,'lost')
   self.assertEqual(load_incident(incident)[2],'first\nsecond')
 def test_interrupted_backup_leaves_no_archive(self):
  from unittest.mock import patch
  from wqi.core import backup_incident
  with tempfile.TemporaryDirectory() as root:
   source=Path(root)/'source.json';source.write_text(json.dumps(BASE));incident=Path(root)/'incident';save_incident(incident,source)
   archive=Path(root)/'backup.zip'
   with patch('wqi.core.zipfile.ZipFile.write',side_effect=OSError('interrupted')):
    with self.assertRaises(OSError):backup_incident(incident,archive)
   self.assertFalse(archive.exists());self.assertEqual(list(Path(root).glob('.wqi-backup-*')),[])
 def test_bad_backup_member_rejected(self):
  import zipfile
  from wqi.core import restore_incident
  with tempfile.TemporaryDirectory() as root:
   archive=Path(root)/'bad.zip'
   with zipfile.ZipFile(archive,'w') as z:
    z.writestr('incident.json','{}');z.writestr('../escape','bad')
   with self.assertRaises(ValueError):restore_incident(archive,Path(root)/'restored')
   self.assertFalse((Path(root)/'escape').exists())
