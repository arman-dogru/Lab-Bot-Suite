#!/usr/bin/python3
"""Read-only deployed-site gate: no tools, credentials, data mutations or model calls."""
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

BASE='http://127.0.0.1:8787'
TITLE='Verify the live project hub'
PROJECT='/projects/6fa11febb2ec'


class Links(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self); self.href=None; self.text=''; self.matches=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a': self.href=dict(attrs).get('href'); self.text=''
    def handle_data(self,data):
        if self.href is not None: self.text+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.href is not None:
            if self.href.startswith(PROJECT) and TITLE in self.text: self.matches.append(self.href)
            self.href=None


request=Request(BASE+'/',headers={'Host':'external.invalid'})
try:
    with urlopen(request,timeout=10) as response: status=response.status
except HTTPError as error: status=error.code
assert status in (400,403),'Unexpected Host accepted with HTTP '+str(status)
with urlopen(BASE+'/?'+urlencode({'q':TITLE}),timeout=10) as response:
    assert response.status==200 and 'text/html' in response.headers.get('Content-Type','')
    parser=Links(); parser.feed(response.read().decode())
assert parser.matches,'Normal GET search did not render a useful matching task link'
print('PASS: deployed Host validation and normal browser GET search')
