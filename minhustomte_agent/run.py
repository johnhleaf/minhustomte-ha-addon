#!/usr/bin/env python3
import os,json,time,logging,threading,uuid,socket,subprocess
from pathlib import Path
from urllib.parse import urlparse,urlencode
import requests, websocket

DATA=Path('/data'); OPT=DATA/'options.json'; CREDS=DATA/'credentials.json'; DASHSTATE=DATA/'dashboard-state.json'
log=logging.getLogger('minhustomte-agent')
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')

def opts():
    try:return json.loads(OPT.read_text())
    except:return {}
def ws_url(http_url,path):
    p=urlparse(http_url.rstrip('/')); return ('wss' if p.scheme=='https' else 'ws')+'://'+p.netloc+path
class Agent:
    def __init__(self):
        self.cfg=opts(); self.server=self.cfg.get('server_url','https://portal.minhustomte.se').rstrip('/')
        self.token=None; self.cabin_id=None; self.hub_id=None; self.ws=None; self.running=True; self.streams={}; self.send_lock=threading.Lock()
        self.load_creds()
        if self.cfg.get('debug'): logging.getLogger().setLevel(logging.DEBUG)
    def load_creds(self):
        if CREDS.exists():
            try:
                c=json.loads(CREDS.read_text()); self.token=c.get('hub_token');self.cabin_id=c.get('cabin_id');self.hub_id=c.get('hub_id');self.server=c.get('server_url',self.server)
            except Exception as e: log.warning('Could not read credentials: %s',e)
    def save_creds(self):
        CREDS.write_text(json.dumps({'hub_token':self.token,'cabin_id':self.cabin_id,'hub_id':self.hub_id,'server_url':self.server},indent=2)); os.chmod(CREDS,0o600)
    def ha_headers(self): return {'Authorization':'Bearer '+os.environ.get('SUPERVISOR_TOKEN',''),'Content-Type':'application/json'}
    def ha_get(self,path,timeout=30):
        r=requests.get('http://supervisor/core/api'+path,headers=self.ha_headers(),timeout=timeout); r.raise_for_status(); return r.json() if 'json' in r.headers.get('content-type','') else r.content
    def ha_post(self,path,payload=None,timeout=30):
        r=requests.post('http://supervisor/core/api'+path,headers=self.ha_headers(),json=payload or {},timeout=timeout); r.raise_for_status(); return r.json() if r.content else {'ok':True}
    def supervisor_info(self):
        try:
            h={'Authorization':'Bearer '+os.environ.get('SUPERVISOR_TOKEN','')}; r=requests.get('http://supervisor/core/info',headers=h,timeout=10); return r.json().get('data',{}) if r.ok else {}
        except:return {}
    def pair(self):
        code=str(self.cfg.get('auth_code','')).strip()
        if not code: raise RuntimeError('Ingen auth_code angiven och hubben är inte parkopplad')
        inf=self.supervisor_info(); payload={'auth_code':code,'hub_id':self.hub_id or ('MHA-'+uuid.uuid4().hex[:12].upper()),'hub_version':'3.1.4','ha_version':inf.get('version')}
        r=requests.post(self.server+'/functions/v1/raspberry-auth',json=payload,timeout=30)
        if not r.ok: raise RuntimeError(f'Parkoppling misslyckades: {r.status_code} {r.text[:300]}')
        d=r.json();self.token=d.get('hub_token') or d.get('token');self.cabin_id=d.get('cabin_id');self.hub_id=d.get('hub_id') or payload['hub_id'];
        if not self.token: raise RuntimeError('Servern returnerade ingen hub_token. MinHustomte server 0.4.3+ krävs.')
        self.save_creds();log.info('Parkopplad till stuga %s som %s',self.cabin_id,self.hub_id)
    def send_json(self,obj):
        with self.send_lock:
            if self.ws and self.ws.sock and self.ws.sock.connected:self.ws.send(json.dumps(obj,separators=(',',':')))
    def send_binary(self,b):
        with self.send_lock:
            if self.ws and self.ws.sock and self.ws.sock.connected:self.ws.send_binary(b)
    def camera_status(self,stream_id,status,method=None,error=None):
        self.send_json({'type':'camera_stream_status','stream_id':stream_id,'status':status,'method':method,'error':error})
    def camera_hls_url(self,entity_id):
        result=self.ha_ws_command({'type':'camera/stream','entity_id':entity_id,'format':'hls'},timeout=25) or {}
        url=result.get('url') if isinstance(result,dict) else None
        if not url: raise RuntimeError('Home Assistant returnerade ingen HLS-adress för kameran')
        if url.startswith('http://') or url.startswith('https://'): return url
        return 'http://supervisor/core'+('/' if not url.startswith('/') else '')+url
    def stream_hls_frames(self,sid,entity,fps,stop,entry):
        self.camera_status(sid,'trying_hls','hls')
        url=self.camera_hls_url(entity)
        token=os.environ.get('SUPERVISOR_TOKEN','')
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-headers',f'Authorization: Bearer {token}\r\n','-i',url,'-an','-vf',f'fps={fps}','-f','image2pipe','-vcodec','mjpeg','-q:v','5','pipe:1']
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        entry['proc']=proc
        buf=b''; first=True
        try:
            while self.running and not stop.is_set():
                chunk=proc.stdout.read(8192) if proc.stdout else b''
                if not chunk:
                    if proc.poll() is not None: break
                    time.sleep(.02); continue
                buf+=chunk
                while True:
                    a=buf.find(b'\xff\xd8')
                    if a<0:
                        if len(buf)>1024*1024: buf=buf[-2:]
                        break
                    b=buf.find(b'\xff\xd9',a+2)
                    if b<0:
                        if a>0: buf=buf[a:]
                        break
                    frame=buf[a:b+2]; buf=buf[b+2:]
                    if frame:
                        self.send_binary(sid.encode('ascii')+frame)
                        if first:
                            self.camera_status(sid,'live','hls'); first=False
            if first and not stop.is_set():
                err=''
                try: err=(proc.stderr.read() if proc.stderr else b'').decode('utf-8','replace')[-700:]
                except: pass
                raise RuntimeError('Home Assistant-livevideo gav inga bildrutor'+((': '+err.strip()) if err.strip() else ''))
        finally:
            try:
                if proc.poll() is None: proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except: pass
            entry['proc']=None
    def entities(self): return self.ha_get('/states')
    def slim_entities(self):
        out=[]
        for e in self.entities():
            a=e.get('attributes') or {}; out.append({'entity_id':e.get('entity_id'),'state':e.get('state'),'attributes':a,'domain':str(e.get('entity_id','')).split('.')[0],'friendly_name':a.get('friendly_name')})
        return out
    def sync_http(self):
        try:
            es=self.slim_entities(); cams=[]
            for e in es:
                if e['domain']=='camera':cams.append({'entity_id':e['entity_id'],'name':e.get('friendly_name') or e['entity_id'],'status':'offline' if e['state']=='unavailable' else 'online','supports_stream':True})
            r=requests.post(self.server+'/api/device/sync',json={'cabin_id':self.cabin_id,'hub_token':self.token,'entities':es,'cameras':cams,'hub_id':self.hub_id,'hub_version':'3.1.4','ha_version':self.supervisor_info().get('version')},timeout=30);
            if not r.ok: raise RuntimeError(f'HTTP {r.status_code} från /api/device/sync: {r.text[:500]}')
            self.send_json({'type':'entity_snapshot','entities':es})
        except Exception as e: log.warning('Entity sync failed: %s',e)
    def ha_ws_command(self,payload,timeout=20):
        token=os.environ.get('SUPERVISOR_TOKEN','')
        if not token: raise RuntimeError('SUPERVISOR_TOKEN saknas')
        ws=websocket.create_connection('ws://supervisor/core/websocket',timeout=timeout)
        try:
            first=json.loads(ws.recv())
            if first.get('type')!='auth_required': raise RuntimeError('Home Assistant WebSocket gav oväntat auth-svar')
            ws.send(json.dumps({'type':'auth','access_token':token}))
            auth=json.loads(ws.recv())
            if auth.get('type')!='auth_ok': raise RuntimeError('Home Assistant WebSocket-autentisering misslyckades')
            msg=dict(payload); msg['id']=int(time.time()*1000)%2000000000
            ws.send(json.dumps(msg,separators=(',',':')))
            while True:
                out=json.loads(ws.recv())
                if out.get('id')!=msg['id']: continue
                if not out.get('success'): raise RuntimeError((out.get('error') or {}).get('message') or 'Home Assistant WebSocket-kommandot misslyckades')
                return out.get('result')
        finally:
            try: ws.close()
            except: pass
    def dashboard_state(self):
        try:return json.loads(DASHSTATE.read_text()) if DASHSTATE.exists() else {}
        except:return {}
    def save_dashboard_state(self,state):
        DASHSTATE.write_text(json.dumps(state,indent=2)); os.chmod(DASHSTATE,0o600)
    def dashboard_list(self):
        return self.ha_ws_command({'type':'lovelace/dashboards/list'}) or []
    def dashboard_get(self,url_path):
        return self.ha_ws_command({'type':'lovelace/config','url_path':url_path})
    def frontend_core_data(self):
        out=self.ha_ws_command({'type':'frontend/get_system_data','key':'core'}) or {}
        value=out.get('value') if isinstance(out,dict) else None
        return value if isinstance(value,dict) else {}
    def dashboard_is_system_default(self,url_path):
        try:return self.frontend_core_data().get('default_panel')==url_path
        except Exception as e:
            log.debug('Kunde inte läsa systemets standarddashboard: %s',e); return False
    def dashboard_set_default(self,url_path,enabled=True):
        core=self.frontend_core_data(); current=core.get('default_panel')
        target=url_path if enabled else ('lovelace' if current==url_path else current)
        if target is None: target='lovelace'
        self.ha_ws_command({'type':'frontend/set_system_data','key':'core','value':{**core,'default_panel':target}})
        return {'system_default':target,'is_system_default':target==url_path}
    def dashboard_status(self,url_path):
        rows=self.dashboard_list(); item=next((x for x in rows if x.get('url_path')==url_path),None); st=self.dashboard_state()
        return {'exists':bool(item),'managed':bool(st.get('managed') and st.get('url_path')==url_path),'dashboard':item,'last_published_at':st.get('last_published_at'),'dashboard_version':st.get('dashboard_version'),'agent_version':'3.1.4','is_system_default':self.dashboard_is_system_default(url_path)}
    def dashboard_apply(self,req):
        url_path=str(req.get('url_path') or 'minhustomte-home'); cfg=req.get('config')
        if not isinstance(cfg,dict) or not isinstance(cfg.get('views'),list): raise RuntimeError('Ogiltig dashboard-konfiguration')
        rows=self.dashboard_list(); existing=next((x for x in rows if x.get('url_path')==url_path),None); st=self.dashboard_state()
        if existing and not (st.get('managed') and st.get('url_path')==url_path): raise RuntimeError('Det finns redan en Home Assistant-dashboard med denna URL som inte ägs av MinHustomte')
        created=False
        if not existing:
            self.ha_ws_command({'type':'lovelace/dashboards/create','url_path':url_path,'title':str(req.get('title') or 'MinHustomte'),'icon':str(req.get('icon') or 'mdi:home-heart'),'show_in_sidebar':bool(req.get('show_in_sidebar',True)),'require_admin':False}); created=True
        else:
            try:self.ha_ws_command({'type':'lovelace/dashboards/update','dashboard_id':existing.get('id'),'title':str(req.get('title') or 'MinHustomte'),'icon':str(req.get('icon') or 'mdi:home-heart'),'show_in_sidebar':bool(req.get('show_in_sidebar',True)),'require_admin':False})
            except Exception as e: log.debug('dashboard metadata update skipped: %s',e)
        self.ha_ws_command({'type':'lovelace/config/save','url_path':url_path,'config':cfg},timeout=30)
        state={'managed':True,'created_by_minhustomte':True,'url_path':url_path,'dashboard_version':req.get('dashboard_version'),'last_published_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        self.save_dashboard_state(state)
        return {'created':created,'url_path':url_path,'managed':True,'dashboard_version':state['dashboard_version'],'last_published_at':state['last_published_at']}
    def dashboard_remove(self,url_path):
        st=self.dashboard_state()
        if not (st.get('managed') and st.get('url_path')==url_path): return {'removed':False,'reason':'not_managed'}
        rows=self.dashboard_list(); existing=next((x for x in rows if x.get('url_path')==url_path),None)
        if existing:
            if self.dashboard_is_system_default(url_path): self.dashboard_set_default(url_path,False)
            self.ha_ws_command({'type':'lovelace/dashboards/delete','dashboard_id':existing.get('id')})
        try:DASHSTATE.unlink()
        except FileNotFoundError:pass
        return {'removed':bool(existing),'url_path':url_path}
    def rpc(self,req):
        action=req.get('action')
        if action=='dashboard_status': return self.dashboard_status(str(req.get('url_path') or 'minhustomte-home'))
        if action=='dashboard_apply': return self.dashboard_apply(req)
        if action=='dashboard_set_default': return self.dashboard_set_default(str(req.get('url_path') or 'minhustomte-home'),req.get('enabled') is not False)
        if action=='dashboard_remove': return self.dashboard_remove(str(req.get('url_path') or 'minhustomte-home'))
        if action=='ping': return {'pong':True,'ts':time.time()}
        if action=='list_entities':
            es=self.slim_entities(); f=req.get('filter') or {}; return {'entities':[e for e in es if not f.get('domain') or e['domain']==f['domain']],'count':len(es)}
        if action=='get_state': return self.ha_get('/states/'+req['entity_id'])
        if action=='get_states': return self.entities()
        if action=='call_service': return self.ha_post('/services/'+req['domain']+'/'+req['service'],req.get('service_data') or {})
        if action=='diagnostics': return {'hub_id':self.hub_id,'agent_version':'3.1.4','ha':self.supervisor_info(),'hostname':socket.gethostname()}
        if action=='get_camera_snapshot':
            r=requests.get('http://supervisor/core/api/camera_proxy/'+req['entity_id'],headers=self.ha_headers(),timeout=20)
            if not r.ok: raise RuntimeError(f'Home Assistant camera_proxy svarade HTTP {r.status_code}: {r.text[:240]}')
            import base64;return {'content_type':r.headers.get('content-type','image/jpeg'),'base64':base64.b64encode(r.content).decode()}
        if action=='start_camera_stream': self.start_stream(req); return {'started':True,'stream_id':req['stream_id']}
        if action=='stop_camera_stream': self.stop_stream(req.get('stream_id')); return {'stopped':True}
        raise RuntimeError('Okänd åtgärd: '+str(action))
    def start_stream(self,req):
        sid=req['stream_id']; self.stop_stream(sid)
        stop=threading.Event(); entry={'stop':stop,'proc':None}; self.streams[sid]=entry
        fps=max(1,min(int(req.get('fps') or self.cfg.get('camera_fps',4)),8)); entity=req['entity_id']
        def loop():
            snapshot_error=None
            self.camera_status(sid,'trying_snapshot','snapshot')
            # Fast path: native/still image endpoint. Some Hikvision entities only expose a usable stream,
            # so a 500 here is not fatal; we fall back to Home Assistant's HLS stream below.
            consecutive=0; sent=0
            while self.running and not stop.is_set() and consecutive<2:
                t=time.time()
                try:
                    r=requests.get('http://supervisor/core/api/camera_proxy/'+entity,headers=self.ha_headers(),timeout=12)
                    ctype=(r.headers.get('content-type') or '').lower()
                    if r.ok and r.content and ('image/' in ctype or r.content[:2]==b'\xff\xd8'):
                        self.send_binary(sid.encode('ascii')+r.content); sent+=1; consecutive=0
                        if sent==1:self.camera_status(sid,'live','snapshot')
                        # Keep snapshot relay if it works; it is cheap and compatible.
                        stop.wait(max(.12,(1.0/fps)-(time.time()-t))); continue
                    snapshot_error=f'camera_proxy HTTP {r.status_code}'
                except Exception as e:
                    snapshot_error=str(e)
                consecutive+=1
                if sent: stop.wait(.5)
            if stop.is_set(): return
            # If snapshots never worked, or stopped working twice, use the actual HA live stream.
            try:
                self.stream_hls_frames(sid,entity,fps,stop,entry)
            except Exception as e:
                if not stop.is_set():
                    detail=str(e)
                    if snapshot_error: detail=f'Stillbild misslyckades ({snapshot_error}). Livevideo misslyckades ({detail}).'
                    log.warning('camera stream %s: %s',entity,detail)
                    self.camera_status(sid,'error','hls',detail)
        threading.Thread(target=loop,daemon=True,name='camera-'+sid[:8]).start()
    def stop_stream(self,sid):
        x=self.streams.pop(sid,None)
        if not x:return
        stop=x.get('stop') if isinstance(x,dict) else x
        if stop:stop.set()
        proc=x.get('proc') if isinstance(x,dict) else None
        if proc:
            try: proc.terminate()
            except: pass
    def on_message(self,ws,msg):
        try:
            m=json.loads(msg)
            if m.get('type')=='rpc':
                rid=m.get('request_id')
                try:d=self.rpc(m.get('request') or {});self.send_json({'type':'rpc_response','request_id':rid,'success':True,'data':d})
                except Exception as e:self.send_json({'type':'rpc_response','request_id':rid,'success':False,'error':str(e)})
        except Exception as e: log.exception('message failed: %s',e)
    def heartbeat_loop(self):
        while self.running:
            try:self.send_json({'type':'heartbeat','hub_id':self.hub_id,'agent_version':'3.1.4','ha_version':self.supervisor_info().get('version')})
            except:pass
            time.sleep(max(10,int(self.cfg.get('heartbeat_interval',30))))
    def sync_loop(self):
        while self.running:self.sync_http();time.sleep(max(60,int(self.cfg.get('entity_sync_interval',300))))
    def run(self):
        if not self.token:self.pair()
        threading.Thread(target=self.heartbeat_loop,daemon=True).start(); threading.Thread(target=self.sync_loop,daemon=True).start()
        backoff=2
        while self.running:
            connected_at=None
            def _opened(ws):
                nonlocal connected_at
                connected_at=time.monotonic(); log.info('WebSocket ansluten')
            try:
                url=ws_url(self.server,'/ws/hub?'+urlencode({'token':self.token}));log.info('Ansluter %s',url.split('?')[0])
                self.ws=websocket.WebSocketApp(url,header=['Sec-WebSocket-Extensions:'],on_message=self.on_message,on_open=_opened,on_error=lambda ws,e:log.warning('WebSocket: %s',e),on_close=lambda ws,c,m:log.warning('WebSocket stängd (%s) %s',c,m))
                # Suppress permessage-deflate negotiation. Headers belong on WebSocketApp, not run_forever().
                self.ws.run_forever(ping_interval=25,ping_timeout=10)
            except Exception as e:log.exception('Connection loop: %s',e)
            stable=connected_at is not None and (time.monotonic()-connected_at)>=30
            if stable: backoff=2
            else: backoff=min(backoff*2,30)
            log.info('Återansluter om %s sekunder',backoff)
            time.sleep(backoff)
if __name__=='__main__':
    while True:
        try:Agent().run()
        except Exception as e:log.exception('Agent stopped: %s',e);time.sleep(10)
