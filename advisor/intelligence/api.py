"""Authenticated read-only API for research call distribution.

Default bind is loopback. Terminate TLS and set network policy at the deployed
reverse proxy. Token digests and tenant roots are operator-provisioned in a
policy file; no credentials are embedded in URLs or source. No mutation routes.
"""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from advisor.intelligence.access import Principal
from advisor.intelligence.adapters import snapshot
from advisor.intelligence.store import CallStore


def dispatch(path, authorization, policy):
    route=urlsplit(path)
    if route.path=='/health':return 200,{'service':'advisor-intelligence','status':'up'}
    if route.query:return 400,{'error':'Query parameters are not supported; use Authorization header'}
    if not authorization or not authorization.startswith('Bearer '):return 401,{'error':'Authentication required'}
    supplied=hashlib.sha256(authorization[7:].encode()).hexdigest()
    member=None
    for token in policy.get('tokens',[]):
        if hmac.compare_digest(supplied,str(token.get('sha256',''))):member=token
    if not member or not member.get('user'):return 401,{'error':'Authentication required'}
    tenant=(policy.get('tenants') or {}).get(member.get('tenant')) or {}
    root=tenant.get('data_root')
    if not root or not Path(root).is_absolute():return 503,{'error':'Tenant is not provisioned'}
    principal=Principal(member['user'],member['tenant'],'viewer')
    data=Path(root)
    pieces=unquote(route.path).strip('/').split('/')
    if pieces==['v1','snapshot']:return 200,snapshot(data,principal=principal)
    if pieces==['v1','calls']:
        return 200,{'tenant':principal.tenant,'calls':snapshot(data,principal=principal)['calls']}
    if len(pieces)==4 and pieces[:2]==['v1','calls'] and pieces[3]=='history':
        if len(pieces[2])!=24 or any(c not in '0123456789abcdef' for c in pieces[2]):return 400,{'error':'Invalid call ID'}
        if not (data/'intelligence'/'calls.sqlite').exists():return 404,{'error':'Call not found'}
        with CallStore(data/'intelligence'/'calls.sqlite',principal,readonly=True) as store:history=store.history(pieces[2])
        return (200,{'history':history}) if history else (404,{'error':'Call not found'})
    return 404,{'error':'Endpoint not found'}


def handler_for(policy_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                policy=json.loads(Path(policy_path).read_text())
                status,payload=dispatch(self.path,self.headers.get('Authorization'),policy)
                body=json.dumps(payload,allow_nan=False).encode()
            except Exception:
                status,body=503,b'{"error":"Research service unavailable"}'
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)))
            if status==401:self.send_header('WWW-Authenticate','Bearer')
            self.end_headers();self.wfile.write(body)
        def do_POST(self):self.send_error(405,'Read-only service')
        def log_message(self,format,*args):pass  # no token, query or private path logging
    return Handler


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--policy',type=Path,required=True)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8517)
    a=p.parse_args()
    server=ThreadingHTTPServer((a.host,a.port),handler_for(a.policy))
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
