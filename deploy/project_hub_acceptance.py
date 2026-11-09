#!/usr/bin/python3
"""Exercise deployed Project Hub using browser-style HTTP, without model calls."""
import hashlib
import http.cookiejar
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

BASE='http://127.0.0.1:8787'
PROJECT='6fa11febb2ec'
PAGE='/projects/'+PROJECT
DATA=b'Project Hub browser acceptance artifact.\nExpected marker: JETSON-HUB-ARTIFACT-PASS\nThis file verifies upload, download, and backup restoration.\n'
FILENAME='project-hub-check.txt'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()),NoRedirect())


def request(path,body=None,headers=None):
    req=Request(urljoin(BASE,path),data=body,headers=headers or {})
    try:
        with client.open(req,timeout=15) as response:
            return response.status,response.headers,response.read()
    except HTTPError as error:
        return error.code,error.headers,error.read()


class Links(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.href=None; self.text=''; self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.href=dict(attrs).get('href'); self.text=''
    def handle_data(self,data):
        if self.href is not None: self.text+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.href is not None:
            self.links.append((self.text.strip(),self.href)); self.href=None


assert request('/')[0]==200
boundary='JetsonHubAcceptanceBoundary'
body=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+FILENAME+
      '"\r\nContent-Type: text/plain\r\n\r\n').encode()+DATA+('\r\n--'+boundary+'--\r\n').encode()
status,_,page=request(PAGE)
assert status==200
if FILENAME.encode() not in page:
    status,_,result=request(PAGE+'/documents',body,{'Origin':BASE,'Content-Type':'multipart/form-data; boundary='+boundary})
    assert status==303,(status,result[:120])
status,_,page=request(PAGE)
assert status==200 and b'Operating notes' in page and b'Verify the live project hub' in page
links=Links(); links.feed(page.decode())
download=next(href for label,href in links.links if label==FILENAME)
assert download.startswith('/')
status,headers,returned=request(download)
assert status==200 and returned==DATA
assert FILENAME in headers.get('Content-Disposition','')
print('PASS: browser-style multipart upload and byte-identical download; SHA256 '+hashlib.sha256(DATA).hexdigest())

token=Path('/var/lib/project-hub/secrets/webhook.token').read_text().strip()
payload=json.dumps({'project_id':PROJECT,'title':'Webhook acceptance check',
                    'details':'Created by the authenticated local webhook test.','status':'done','priority':'low'}).encode()
assert request('/webhook/tasks',payload,{'Content-Type':'application/json'})[0]==401
assert request('/webhook/tasks',payload,{'Content-Type':'application/json','Authorization':'Bearer invalid'})[0]==401
assert request('/webhook/tasks',json.dumps({'project_id':PROJECT,'title':''}).encode(),
               {'Content-Type':'application/json','Authorization':'Bearer '+token})[0]==400
if b'Webhook acceptance check' not in page:
    status,_,result=request('/webhook/tasks',payload,{'Content-Type':'application/json','Authorization':'Bearer '+token})
    assert status==201,(status,result[:120])
assert request(PAGE+'/tasks',urlencode({'title':'Cross-origin rejection check'}).encode(),
               {'Content-Type':'application/x-www-form-urlencoded','Origin':'https://external.invalid'})[0]==403
print('PASS: authenticated webhook creates a task; missing/wrong credentials and cross-origin writes rejected')

assert b'Verify the live project hub' in request('/?'+urlencode({'q':'Verify the live project hub'}))[2]
status,_,export=request(PAGE+'/export.json')
assert status==200 and isinstance(json.loads(export.decode()),dict)
assert b'Webhook acceptance check' in export and FILENAME.encode() in export
status,_,markdown=request(PAGE+'/export.md')
assert status==200 and b'Operating notes' in markdown and FILENAME.encode() in markdown
print('PASS: search and JSON/Markdown exports contain saved project data')

subprocess.run(['systemctl','restart','jetson-project-hub.service'],check=True)
import time
for attempt in range(30):
    try:
        if request('/health')[0]==200: break
    except Exception: pass
    time.sleep(1)
else: raise AssertionError('App did not restart')
status,_,page=request(PAGE)
assert status==200 and b'Operating notes' in page and b'Webhook acceptance check' in page
assert request(download)[2]==DATA
print('PASS: project, tasks, note revisions and uploaded bytes survive app service restart')
assert request('/',headers={'Host':'external.invalid'})[0] in (400,403), 'Unexpected Host was accepted'
print('PASS: unexpected Host headers rejected')
