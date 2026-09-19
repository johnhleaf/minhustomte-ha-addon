#!/usr/bin/env python3
import os,json,time,logging,threading,uuid,socket,subprocess,base64,tempfile,hashlib
from pathlib import Path
from io import BytesIO
from urllib.parse import urlparse,urlencode,urlsplit,urlunsplit,quote
import requests, websocket
from PIL import Image,ImageDraw,ImageChops,ImageOps
from requests.auth import HTTPDigestAuth
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

DATA=Path('/data'); OPT=DATA/'options.json'; CREDS=DATA/'credentials.json'; DASHSTATE=DATA/'dashboard-state.json'; HIKCFG=DATA/'hikvision-playback.json'
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
        self.token=None; self.cabin_id=None; self.hub_id=None; self.ws=None; self.running=True; self.streams={}; self.send_lock=threading.Lock(); self.ai_watch=[]; self.ai_lock=threading.Lock(); self.ai_last_trigger={}
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
        inf=self.supervisor_info(); payload={'auth_code':code,'hub_id':self.hub_id or ('MHA-'+uuid.uuid4().hex[:12].upper()),'hub_version':'3.1.12','ha_version':inf.get('version')}
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
            if self.ws and self.ws.sock and self.ws.sock.connected:self.ws.send(b, opcode=websocket.ABNF.OPCODE_BINARY)
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
        safe_url=url.split('?',1)[0]
        log.info('camera %s: starting HLS relay via %s',entity,safe_url)
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
                rc=proc.poll()
                suffix=((': '+err.strip()) if err.strip() else '')
                raise RuntimeError(f'Home Assistant-livevideo gav inga bildrutor (ffmpeg exit={rc})'+suffix)
        finally:
            try:
                if proc.poll() is None: proc.terminate(); proc.wait(timeout=2)
            except Exception:
                try: proc.kill()
                except: pass
            entry['proc']=None
    def hikvision_configs(self):
        try:
            data=json.loads(HIKCFG.read_text()) if HIKCFG.exists() else {}
            return data if isinstance(data,dict) else {}
        except Exception as e:
            log.warning('Could not read Hikvision playback config: %s',e); return {}
    def save_hikvision_configs(self,data):
        HIKCFG.write_text(json.dumps(data,indent=2)); os.chmod(HIKCFG,0o600)
    def hikvision_config(self,entity_id):
        cfg=self.hikvision_configs().get(entity_id)
        if not isinstance(cfg,dict): raise RuntimeError('Inspelningar är inte konfigurerade för den här kameran')
        if not cfg.get('address') or not cfg.get('username') or not cfg.get('password'): raise RuntimeError('Hikvision-adress/användare/lösenord saknas')
        return cfg
    def hikvision_base_url(self,cfg):
        address=str(cfg.get('address') or '').strip().rstrip('/')
        if '://' in address:
            p=urlparse(address); scheme=p.scheme; host=p.hostname or ''; port=p.port
        else:
            scheme='https' if cfg.get('https') else 'http'; host=address; port=None
        if not host: raise RuntimeError('Ogiltig Hikvision-adress')
        if port is None: port=int(cfg.get('http_port') or (443 if scheme=='https' else 80))
        default=(scheme=='http' and port==80) or (scheme=='https' and port==443)
        return f'{scheme}://{host}' + ('' if default else f':{port}')
    def hikvision_auth(self,cfg): return HTTPDigestAuth(str(cfg.get('username')),str(cfg.get('password')))
    def _xml_local(self,tag): return str(tag).split('}',1)[-1]
    def hikvision_recordings(self,entity_id,date_str):
        cfg=self.hikvision_config(entity_id); base=self.hikvision_base_url(cfg)
        try: day=datetime.strptime(date_str,'%Y-%m-%d').replace(tzinfo=ZoneInfo('Europe/Stockholm'))
        except: raise RuntimeError('Ogiltigt datum')
        start=day.astimezone(timezone.utc); end=(day+timedelta(days=1)).astimezone(timezone.utc)
        track=str(cfg.get('track_id') or '101'); sid=uuid.uuid4().hex
        stxt=start.strftime('%Y-%m-%dT%H:%M:%SZ'); etxt=end.strftime('%Y-%m-%dT%H:%M:%SZ')
        xml=(f'<CMSearchDescription><searchID>{sid}</searchID><trackList><trackID>{track}</trackID></trackList>'
             f'<timeSpanList><timeSpan><startTime>{stxt}</startTime><endTime>{etxt}</endTime></timeSpan></timeSpanList>'
             '<maxResults>200</maxResults><searchResultPostion>0</searchResultPostion>'
             '<metadataList><metadataDescriptor>//recordType.meta.std-cgi.com</metadataDescriptor></metadataList></CMSearchDescription>')
        r=requests.post(base+'/ISAPI/ContentMgmt/search',data=xml.encode(),headers={'Content-Type':'application/xml'},auth=self.hikvision_auth(cfg),timeout=25,verify=False)
        if not r.ok: raise RuntimeError(f'Hikvision ISAPI search svarade HTTP {r.status_code}: {r.text[:240]}')
        try: root=ET.fromstring(r.content)
        except Exception as e: raise RuntimeError('Kunde inte tolka Hikvision söksvar: '+str(e))
        items=[]
        for node in root.iter():
            if self._xml_local(node.tag) not in ('searchMatchItem','matchElement'): continue
            vals={}
            for x in node.iter():
                key=self._xml_local(x.tag); txt=(x.text or '').strip()
                if txt and key not in vals: vals[key]=txt
            st=vals.get('startTime'); en=vals.get('endTime')
            if not st or not en: continue
            try:
                ds=datetime.fromisoformat(st.replace('Z','+00:00')); de=datetime.fromisoformat(en.replace('Z','+00:00')); dur=max(0,int((de-ds).total_seconds()))
                local_s=ds.astimezone(ZoneInfo('Europe/Stockholm')).isoformat(); local_e=de.astimezone(ZoneInfo('Europe/Stockholm')).isoformat()
            except: dur=0; local_s=st; local_e=en
            uri=vals.get('playbackURI') or vals.get('playbackUri') or ''
            kind=vals.get('metadataDescriptor') or vals.get('recordType') or 'recording'
            rid=hashlib.sha1((st+'|'+en+'|'+uri).encode()).hexdigest()[:16]
            items.append({'id':rid,'start':st,'end':en,'start_local':local_s,'end_local':local_e,'duration_seconds':dur,'type':kind,'playback_uri':uri})
        items.sort(key=lambda x:x.get('start',''))
        return {'date':date_str,'count':len(items),'recordings':items,'source':'Hikvision SD-kort via ISAPI'}
    def hikvision_rtsp_uri(self,cfg,start,end,playback_uri=''):
        uri=str(playback_uri or '').strip(); rtsp_port=int(cfg.get('rtsp_port') or 554); track=str(cfg.get('track_id') or '101')
        address=str(cfg.get('address') or '').strip(); host=urlparse(address).hostname if '://' in address else address.split(':')[0]
        if not uri:
            def compact(v):
                d=datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(timezone.utc)
                return d.strftime('%Y%m%dT%H%M%SZ')
            uri=f'rtsp://{host}:{rtsp_port}/Streaming/tracks/{track}?starttime={compact(start)}&endtime={compact(end)}'
        p=urlsplit(uri)
        if p.scheme.lower()!='rtsp': raise RuntimeError('Hikvision returnerade en ogiltig playback-URI')
        hostname=p.hostname or host; port=p.port or rtsp_port
        user=quote(str(cfg.get('username')),safe=''); pw=quote(str(cfg.get('password')),safe='')
        netloc=f'{user}:{pw}@{hostname}:{port}'
        return urlunsplit(('rtsp',netloc,p.path,p.query,p.fragment))
    def hikvision_fetch_recording(self,entity_id,start,end,playback_uri=''):
        cfg=self.hikvision_config(entity_id)
        try:
            ds=datetime.fromisoformat(str(start).replace('Z','+00:00')); de=datetime.fromisoformat(str(end).replace('Z','+00:00')); duration=(de-ds).total_seconds()
        except: raise RuntimeError('Ogiltig start- eller sluttid')
        if duration<=0 or duration>900: raise RuntimeError('Klippet måste vara mellan 1 sekund och 15 minuter')
        uri=self.hikvision_rtsp_uri(cfg,start,end,playback_uri)
        fd,tmp=tempfile.mkstemp(prefix='mht-hik-',suffix='.mp4'); os.close(fd)
        try:
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-rtsp_transport','tcp','-i',uri,'-t',str(min(duration+3,903)),'-map','0:v:0','-map','0:a?','-c','copy','-movflags','+faststart','-y',tmp]
            proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=min(180,max(45,int(duration)+35)))
            if proc.returncode!=0 or not os.path.exists(tmp) or os.path.getsize(tmp)<1024:
                err=(proc.stderr or b'').decode('utf-8','replace')[-700:]
                raise RuntimeError('Hikvision playback kunde inte hämtas'+((': '+err.strip()) if err.strip() else ''))
            size=os.path.getsize(tmp)
            if size>48*1024*1024: raise RuntimeError('Klippet är större än 48 MB. Välj ett kortare klipp.')
            data=Path(tmp).read_bytes()
            return {'content_type':'video/mp4','size':len(data),'base64':base64.b64encode(data).decode()}
        finally:
            try: os.unlink(tmp)
            except: pass
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
                if e['domain']=='camera':cams.append({'entity_id':e['entity_id'],'name':e.get('friendly_name') or e['entity_id'],'status':'offline' if str(e.get('state') or '').lower() in ('unavailable','unknown','') else 'online','supports_stream':True})
            r=requests.post(self.server+'/api/device/sync',json={'cabin_id':self.cabin_id,'hub_token':self.token,'entities':es,'cameras':cams,'hub_id':self.hub_id,'hub_version':'3.1.12','ha_version':self.supervisor_info().get('version')},timeout=30);
            if not r.ok: raise RuntimeError(f'HTTP {r.status_code} från /api/device/sync: {r.text[:500]}')
            try:
                d=r.json(); watch=d.get('camera_ai_watch') or []
                if isinstance(watch,list):
                    with self.ai_lock:self.ai_watch=watch
            except Exception: pass
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
        return {'exists':bool(item),'managed':bool(st.get('managed') and st.get('url_path')==url_path),'dashboard':item,'last_published_at':st.get('last_published_at'),'dashboard_version':st.get('dashboard_version'),'agent_version':'3.1.12','is_system_default':self.dashboard_is_system_default(url_path)}
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
    def camera_ai_snapshot(self,entity_id):
        r=requests.get('http://supervisor/core/api/camera_proxy/'+entity_id,headers=self.ha_headers(),timeout=20)
        if not r.ok or not r.content: raise RuntimeError(f'Home Assistant camera_proxy svarade HTTP {r.status_code}')
        ctype=(r.headers.get('content-type') or 'image/jpeg').split(';')[0].strip().lower()
        if ctype not in ('image/jpeg','image/png','image/webp'):
            if r.content[:2]==b'\xff\xd8': ctype='image/jpeg'
            else: raise RuntimeError('Kameran returnerade inte en bild')
        if len(r.content)>8*1024*1024: raise RuntimeError('Kamerabilden är större än 8 MB')
        return {'content_type':ctype,'base64':base64.b64encode(r.content).decode(),'captured_at':datetime.now(timezone.utc).isoformat()}
    def camera_ai_image(self,raw):
        im=Image.open(BytesIO(base64.b64decode(raw['base64'])))
        im=ImageOps.exif_transpose(im).convert('RGB')
        return im
    def camera_ai_roi(self,im,zone,long_side):
        if not isinstance(zone,list) or len(zone)<3:return None
        w,h=im.size
        points=[(round(max(0,min(1,float(p[0])))*w),round(max(0,min(1,float(p[1])))*h)) for p in zone]
        left=max(0,min(x for x,y in points));top=max(0,min(y for x,y in points));right=min(w,max(x for x,y in points));bottom=min(h,max(y for x,y in points))
        if right-left<12 or bottom-top<12:return None
        crop=im.crop((left,top,right,bottom));mask=Image.new('L',crop.size,0)
        ImageDraw.Draw(mask).polygon([(x-left,y-top) for x,y in points],fill=255)
        background=Image.new('RGB',crop.size,(0,0,0));background.paste(crop,(0,0),mask)
        background.thumbnail((long_side,long_side),Image.Resampling.LANCZOS)
        output=BytesIO();background.save(output,format='JPEG',quality=83,optimize=True)
        return base64.b64encode(output.getvalue()).decode()
    def camera_ai_motion_in_zone(self,images,zone):
        if len(images)<2 or not isinstance(zone,list) or len(zone)<3:return True
        im1=self.camera_ai_image(images[0]);im2=self.camera_ai_image(images[-1]);im1.thumbnail((192,108));im2=im2.resize(im1.size)
        w,h=im1.size;mask=Image.new('1',(w,h),0)
        ImageDraw.Draw(mask).polygon([(round(float(x)*w),round(float(y)*h)) for x,y in zone],fill=1)
        delta=ImageChops.difference(im1.convert('L'),im2.convert('L'))
        pixels=list(delta.getdata());area=list(mask.getdata());n=sum(area)
        return True if n<30 else sum(1 for v,within in zip(pixels,area) if within and v>=28)/n>=0.018
    def camera_ai_capture(self,req):
        entity=str(req.get('entity_id') or '')
        if not entity.startswith('camera.'): raise RuntimeError('Ogiltig kamera för bildinsamling')
        count=max(1,min(int(req.get('capture_count') or 3),8)); interval=max(150,min(int(req.get('capture_interval_ms') or 500),3000))/1000.0
        delay=max(0,min(int(req.get('capture_delay_ms') or 0),30000))/1000.0
        if delay: time.sleep(delay)
        images=[]
        for i in range(count):
            images.append(self.camera_ai_snapshot(entity))
            if i<count-1: time.sleep(interval)
        vehicle_zone=req.get('vehicle_zone');plate_zone=req.get('plate_zone');motion_zone=req.get('motion_zone')
        if req.get('motion_filter_enabled') and isinstance(motion_zone,list) and len(motion_zone)>=3:
            if count<2:
                time.sleep(0.4);images.append(self.camera_ai_snapshot(entity))
            if not self.camera_ai_motion_in_zone(images,motion_zone):
                return {'entity_id':entity,'filtered_motion':True,'images':[]}
        if vehicle_zone or plate_zone:
            for snapshot in images:
                try:
                    image=self.camera_ai_image(snapshot)
                    if vehicle_zone:snapshot['ai_vehicle_base64']=self.camera_ai_roi(image,vehicle_zone,768)
                    if plate_zone:snapshot['ai_plate_base64']=self.camera_ai_roi(image,plate_zone,1024)
                except Exception as e:log.warning('AI-beskärning misslyckades, original används: %s',e)
        return {'entity_id':entity,'trigger_entity_id':req.get('trigger_entity_id'),'trigger_mode':req.get('trigger_mode') or 'manual','trigger_state':req.get('trigger_state'),'trigger_payload':req.get('trigger_payload') or {},'captured_at':datetime.now(timezone.utc).isoformat(),'images':images}
    def camera_ai_send_capture(self,watch,event):
        entity=watch.get('entity_id'); trigger=watch.get('trigger_entity_id')
        key=f'{entity}|{trigger}'; now=time.monotonic()
        if now-self.ai_last_trigger.get(key,0)<5:return
        self.ai_last_trigger[key]=now
        try:
            data=self.camera_ai_capture({'entity_id':entity,'capture_count':watch.get('capture_count'),'capture_interval_ms':watch.get('capture_interval_ms'),'capture_delay_ms':watch.get('capture_delay_ms'),'vehicle_zone':watch.get('vehicle_zone'),'plate_zone':watch.get('plate_zone'),'motion_zone':watch.get('motion_zone'),'motion_filter_enabled':watch.get('motion_filter_enabled'),'trigger_entity_id':trigger,'trigger_mode':watch.get('trigger_mode'),'trigger_state':event.get('new_state'),'trigger_payload':event})
            if data.get('filtered_motion'):
                log.info('AI-rörelse filtrerades bort utanför markerat område: %s',entity);return
            data.update({'type':'camera_ai_capture','capture_id':uuid.uuid4().hex,'source':'ha_state_changed'})
            self.send_json(data); log.info('AI-kamerahändelse %s <- %s: %s bilder',entity,trigger,len(data.get('images') or []))
        except Exception as e: log.warning('AI-bildinsamling %s misslyckades: %s',entity,e)
    def camera_ai_event_matches(self,watch,entity_id,old_state,new_state):
        if not watch.get('enabled',True) or not watch.get('trigger_entity_id') or watch.get('trigger_entity_id')!=entity_id:return False
        mode=str(watch.get('trigger_mode') or 'vehicle_event')
        if mode=='manual':return False
        if old_state==new_state:return False
        domain=entity_id.split('.',1)[0]
        if domain=='binary_sensor': return str(new_state).lower() in ('on','true','open','detected','motion')
        if str(new_state).lower() in ('unknown','unavailable','none',''):return False
        return True
    def camera_ai_event_loop(self):
        token=os.environ.get('SUPERVISOR_TOKEN','')
        while self.running:
            ws=None
            try:
                ws=websocket.create_connection('ws://supervisor/core/websocket',timeout=65)
                first=json.loads(ws.recv())
                if first.get('type')!='auth_required': raise RuntimeError('Home Assistant WebSocket gav oväntat auth-svar')
                ws.send(json.dumps({'type':'auth','access_token':token})); auth=json.loads(ws.recv())
                if auth.get('type')!='auth_ok': raise RuntimeError('Home Assistant WebSocket-auth misslyckades')
                ws.send(json.dumps({'id':811733,'type':'subscribe_events','event_type':'state_changed'}))
                while self.running:
                    msg=json.loads(ws.recv())
                    if msg.get('type')!='event':continue
                    ev=(msg.get('event') or {}).get('data') or {}; eid=str(ev.get('entity_id') or ''); old=(ev.get('old_state') or {}).get('state'); ns=ev.get('new_state') or {}; new=ns.get('state')
                    with self.ai_lock: watches=list(self.ai_watch)
                    payload={'entity_id':eid,'old_state':old,'new_state':new,'attributes':ns.get('attributes') or {},'time_fired':(msg.get('event') or {}).get('time_fired')}
                    for watch in watches:
                        if self.camera_ai_event_matches(watch,eid,old,new): threading.Thread(target=self.camera_ai_send_capture,args=(watch,payload),daemon=True).start()
            except Exception as e:
                log.warning('AI trigger event stream: %s',e); time.sleep(5)
            finally:
                try:
                    if ws:ws.close()
                except:pass
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
        if action=='diagnostics': return {'hub_id':self.hub_id,'agent_version':'3.1.12','ha':self.supervisor_info(),'hostname':socket.gethostname()}
        if action=='camera_ai_config_refresh':
            self.sync_http(); return {'success':True,'watch_count':len(self.ai_watch)}
        if action=='camera_ai_preview': return self.camera_ai_snapshot(str(req.get('entity_id') or ''))
        if action=='camera_ai_capture_now': return self.camera_ai_capture(req)
        if action=='get_camera_snapshot':
            r=requests.get('http://supervisor/core/api/camera_proxy/'+req['entity_id'],headers=self.ha_headers(),timeout=20)
            if not r.ok: raise RuntimeError(f'Home Assistant camera_proxy svarade HTTP {r.status_code}: {r.text[:240]}')
            import base64;return {'content_type':r.headers.get('content-type','image/jpeg'),'base64':base64.b64encode(r.content).decode()}
        if action=='start_camera_stream': self.start_stream(req); return {'started':True,'stream_id':req['stream_id']}
        if action=='stop_camera_stream': self.stop_stream(req.get('stream_id')); return {'stopped':True}
        if action=='hikvision_playback_config_get':
            cfg=self.hikvision_configs().get(req.get('entity_id')) or {}
            return {'configured':bool(cfg.get('address') and cfg.get('username') and cfg.get('password')),'address':cfg.get('address',''),'username':cfg.get('username',''),'http_port':cfg.get('http_port',80),'https':bool(cfg.get('https')),'rtsp_port':cfg.get('rtsp_port',554),'track_id':cfg.get('track_id','101'),'password_saved':bool(cfg.get('password'))}
        if action=='hikvision_playback_config_set':
            entity=str(req.get('entity_id') or ''); address=str(req.get('address') or '').strip(); username=str(req.get('username') or '').strip()
            if not entity.startswith('camera.') or not address or not username: raise RuntimeError('Kameraadress, entity och användarnamn krävs')
            allcfg=self.hikvision_configs(); old=allcfg.get(entity) or {}; password=str(req.get('password') or '') or str(old.get('password') or '')
            if not password: raise RuntimeError('Lösenord krävs första gången')
            cfg={'address':address,'username':username,'password':password,'http_port':max(1,min(int(req.get('http_port') or 80),65535)),'https':bool(req.get('https')),'rtsp_port':max(1,min(int(req.get('rtsp_port') or 554),65535)),'track_id':str(req.get('track_id') or '101')[:16]}
            base=self.hikvision_base_url(cfg); test=requests.get(base+'/ISAPI/System/deviceInfo',auth=self.hikvision_auth(cfg),timeout=12,verify=False)
            if not test.ok: raise RuntimeError(f'Kunde inte logga in på Hikvision (HTTP {test.status_code})')
            allcfg[entity]=cfg; self.save_hikvision_configs(allcfg); return {'success':True,'configured':True,'address':address,'username':username,'password_saved':True,'track_id':cfg['track_id']}
        if action=='hikvision_recordings_list': return self.hikvision_recordings(req['entity_id'],req['date'])
        if action=='hikvision_recording_fetch': return self.hikvision_fetch_recording(req['entity_id'],req['start'],req['end'],req.get('playback_uri') or '')
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
            try:self.send_json({'type':'heartbeat','hub_id':self.hub_id,'agent_version':'3.1.12','ha_version':self.supervisor_info().get('version')})
            except:pass
            time.sleep(max(10,int(self.cfg.get('heartbeat_interval',30))))
    def sync_loop(self):
        while self.running:self.sync_http();time.sleep(max(60,int(self.cfg.get('entity_sync_interval',300))))
    def run(self):
        if not self.token:self.pair()
        threading.Thread(target=self.heartbeat_loop,daemon=True).start(); threading.Thread(target=self.sync_loop,daemon=True).start(); threading.Thread(target=self.camera_ai_event_loop,daemon=True,name='camera-ai-events').start()
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
