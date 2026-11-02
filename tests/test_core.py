import copy
import json
import tempfile
import unittest
from pathlib import Path
from wqi.core import compare, findings, load_incident, read_snapshot, report, save_incident, validate

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
 def test_partial_does_not_prove_disappearance(self):
  later=copy.deepcopy(BASE);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z'
  later['jobs']=later['jobs'][1:];later['coverage']['action']={'complete':False,'source_count':3}
  self.assertIn('unknown-partial-capture',[x['change'] for x in compare(BASE,later)['rows']])
 def test_recorded_completion(self):
  later=copy.deepcopy(BASE);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z';later['jobs'][0]['status']='complete'
  self.assertIn('progressed',[x['change'] for x in compare(BASE,later)['rows']])
 def test_ambiguous_cron(self):
  x=copy.deepcopy(BASE);x['jobs'].append(dict(x['jobs'][-1],id='c2'));x['coverage']['cron']['source_count']=2
  later=copy.deepcopy(x);later['captured_start']='2026-11-01T13:00:00Z';later['captured_end']='2026-11-01T13:01:00Z'
  self.assertIn('ambiguous',[r['change'] for r in compare(x,later)['rows']])
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
 def test_html_escaped_and_incident_hashes(self):
  x=copy.deepcopy(BASE);x['jobs'][0]['hook']='<script>alert(1)</script>'
  self.assertNotIn('<script>',report(x,notes='<script>',format='html'))
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
  x['logs']=[{'action_id':'300','message':'redacted','time':'2026-11-01T12:00:00Z'}]
  x['coverage']['action']['source_count']=301;x['coverage']['cron']['source_count']=0
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'input.json';p.write_text(json.dumps(x))
   with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
   proc=subprocess.Popen([sys.executable,'-m','wqi','serve',str(p),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    for _ in range(40):
     try:
      response=urllib.request.urlopen(f'http://127.0.0.1:{port}/?page=4',timeout=.2);break
     except Exception:time.sleep(.05)
    body=response.read().decode();self.assertIn('>300</td>',body);self.assertNotIn('>299</td>',body)
    self.assertIn('Action log timeline',urllib.request.urlopen(f'http://127.0.0.1:{port}/logs').read().decode())
    request=urllib.request.Request(f'http://127.0.0.1:{port}/',headers={'Host':'evil.example'})
    with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(request)
    self.assertEqual(ctx.exception.code,403)
   finally:proc.terminate();proc.wait(timeout=3)
