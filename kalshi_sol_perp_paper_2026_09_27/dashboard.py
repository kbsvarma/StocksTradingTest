"""Read-only LAN status page for the paper experiment."""
import json,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parent
HTML='''<!doctype html><meta charset="utf-8"><title>SOL perpetual paper pilot</title><style>body{background:#101722;color:#e7eef9;font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}h1{font-size:28px}.tag{color:#ffcb66}pre{white-space:pre-wrap;background:#182332;padding:20px;border-radius:10px}a{color:#83beff}</style><h1>SOL perpetual paper pilot</h1><p class="tag">PAPER ONLY · No real orders · No account credentials</p><p>24-hour breakout · 12-hour channel exit · volatility stop · maximum $50 simulated exposure</p><p id="health">Loading…</p><pre id="summary"></pre><details><summary>Full experiment status</summary><pre id="full"></pre></details><p>Fees and fills are estimates. Live order-book depth is recorded; queue position and real executions are not simulated. No historical profits are inserted into this ledger.</p><script>async function refresh(){try{let s=await(await fetch('/status.json',{cache:'no-store'})).json();let age=Date.now()/1000-s.heartbeat;document.getElementById('health').textContent=(age<90?'RUNNING':'STALE')+' · updated '+Math.round(age)+' seconds ago'+(s.error?' · ERROR: '+s.error:'');let next=new Date((Math.floor(Date.now()/3600000)+1)*3600000).toISOString();document.getElementById('summary').textContent=JSON.stringify({started:new Date(s.started_ts*1000).toISOString(),next_hour:next,completed_paper_trades:s.trades.length,realized_pnl:s.realized_pnl,position:s.position,pending:s.pending,open_pnl:s.open_pnl,latest_signal:s.latest_indicator},null,2);document.getElementById('full').textContent=JSON.stringify(s,null,2)}catch(e){document.getElementById('health').textContent='Status unavailable: '+e}}refresh();setInterval(refresh,15000)</script>'''
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/':data=HTML.encode();kind='text/html; charset=utf-8'
  elif self.path=='/status.json':
   try:data=(ROOT/'state/status.json').read_bytes()
   except FileNotFoundError:self.send_error(503);return
   kind='application/json'
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
if __name__=='__main__':ThreadingHTTPServer(('192.168.3.136',8509),Handler).serve_forever()
