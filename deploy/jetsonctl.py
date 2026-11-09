#!/usr/bin/python3
"""Host recovery controls. Compatible with Python 3.6; no secrets printed."""
import argparse
import getpass
import json
import os
import sqlite3
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path('/etc/jetson-bot')
STATE=Path('/srv/jetson-bot/state')


def run(*args):
    return subprocess.run(args,check=True)


def api(command):
    token=(ROOT/'local-token').read_text().strip()
    path='/'+command if command in ('pause','resume') else '/budget'
    request=Request('http://127.0.0.1:8870'+path,
                    data=b'{}' if command in ('pause','resume') else None,
                    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urlopen(request,timeout=8) as response:
        return json.load(response)


def configure():
    if not os.isatty(0): raise SystemExit('Run configure in an interactive SSH terminal.')
    print('Credentials stay on this Jetson. Input is hidden and never added to command history.')
    public=json.loads((ROOT/'discord.json').read_text())
    for name in ('application_id','server_id','owner_id','channel_id'):
        current=public.get(name) or ''
        if not current.isdigit():
            current=input(name+': ').strip()
        if not current.isdigit(): raise SystemExit('A numeric '+name+' is required.')
        public[name]=current
    print('Using the saved Discord application, private channel and operator IDs.')
    base=(input('Provider base URL [https://api.x.ai/v1]: ').strip() or 'https://api.x.ai/v1').rstrip('/')
    model=input('Model id [grok-4.7]: ').strip() or 'grok-4.7'
    if not base.startswith('https://') or ' ' in base:
        raise SystemExit('Provider URL must be https. Nothing was saved.')
    token=getpass.getpass('Discord BOT TOKEN (hidden): ').strip()
    key=getpass.getpass('Provider API key (hidden): ').strip()
    if not token or not key: raise SystemExit('Both credentials are required; nothing was changed.')
    # Verify the bot and the model list, without creating any inference requests.
    checks=(('https://discord.com/api/v10/users/@me','Bot '+token),
            (base+'/models','Bearer '+key))
    for url,auth in checks:
        request=Request(url,headers={'Authorization':auth,'User-Agent':'JetsonBot/1.0'})
        try:
            with urlopen(request,timeout=25) as response: info=json.load(response)
        except Exception:
            raise SystemExit('Credential validation failed at '+url.split('/')[2]+'. No credentials saved.')
        if 'discord.com' in url and (not info.get('bot') or info.get('id') != public['application_id']):
            raise SystemExit('This token belongs to another bot. No credentials saved.')
        if url.endswith('/models') and not any(x.get('id')==model for x in info.get('data',[])):
            raise SystemExit('That model is not available for this key. No credentials saved.')
    os.umask(0o077)
    budget_path=ROOT/'budget.json'
    if budget_path.exists():
        budget=json.loads(budget_path.read_text())
        budget['model']=model
        budget['upstream']=base
        budget.setdefault('image_tokens',2000)
        budget.setdefault('max_images',4)
        budget.setdefault('max_image_bytes',1500000)
        budget_path.write_text(json.dumps(budget,indent=2)+'\n')
        os.chmod(str(budget_path),0o600)
    (ROOT/'xai-key').write_text(key+'\n')
    (ROOT/'discord.json').write_text(json.dumps(public,indent=2)+'\n')
    local=(ROOT/'local-token').read_text().strip()
    values=dict(JETSON_LOCAL_TOKEN=local,DISCORD_BOT_TOKEN=token,
                HERMES_WRITE_SAFE_ROOT='/opt/data:/workspace',
                DISCORD_ALLOWED_USERS=public['owner_id'],
                GATEWAY_ADMIN_USERS=public['owner_id'],
                DISCORD_HOME_CHANNEL=public.get('status_channel_id',public['channel_id']),
                DISCORD_STATUS_CHANNEL=public.get('status_channel_id',''),
                DISCORD_FREE_RESPONSE_CHANNELS=','.join(filter(None,[public['channel_id'],public.get('status_channel_id')])),
                DISCORD_ALLOWED_CHANNELS=','.join(filter(None,[public['channel_id'],public.get('status_channel_id')])),
                DISCORD_NO_THREAD_CHANNELS=public.get('status_channel_id',''),
                DISCORD_IGNORED_CHANNELS=public.get('activity_channel_id',''),
                DISCORD_ALLOW_ALL_USERS='false',GATEWAY_ALLOW_ALL_USERS='false',
                DISCORD_ALLOW_BOTS='none',API_SERVER_ENABLED='false',HERMES_DASHBOARD='0')
    env=''.join(k+'='+v+'\n' for k,v in values.items())
    for name in ('.env','runtime.env'):
        p=STATE/name
        p.write_text(env)
        os.chmod(str(p),0o600)
        import pwd
        user=pwd.getpwnam('jetsonbot')
        os.chown(str(p),user.pw_uid,user.pw_gid)
    channels=STATE/'discord-channels.json'
    channels.write_text(json.dumps(public,indent=2)+'\n')
    os.chmod(str(channels),0o600)
    os.chown(str(channels),user.pw_uid,user.pw_gid)
    (ROOT/'ready').touch()
    run('systemctl','restart','jetson-budget.service','jetson-bot.service')
    if (Path('/etc/systemd/system')/'jetson-discord-watch.service').exists():
        run('systemctl','restart','jetson-discord-watch.service')
    print('Credentials saved; services started. Run sudo jetsonctl status, then test /jetson status in Discord.')


def backup():
    # A timer can catch up during boot while the budget service is still
    # waiting for verified time. Wait before stopping a healthy agent.
    deadline=time.monotonic()+180
    while True:
        try:
            api('budget')
            break
        except Exception:
            if time.monotonic()>=deadline:
                raise SystemExit('Budget service not ready; backup deferred without stopping the bot.')
            time.sleep(2)
    target=Path('/var/backups/jetson-bot')
    target.mkdir(parents=True,exist_ok=True)
    os.chmod(str(target),0o700)
    services=('jetson-bot.service','jetson-discord-watch.service',
              'jetson-project-hub.service')
    running=[name for name in services if
             subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0]
    try:
        if running: run('systemctl','stop',*running)
        units=ROOT/'systemd-backup'
        units.mkdir(exist_ok=True)
        os.chmod(str(units),0o700)
        for pattern in ('jetson-*.service','jetson-*.timer'):
            for unit in Path('/etc/systemd/system').glob(pattern):
                if unit.is_file():
                    shutil.copyfile(str(unit),str(units/unit.name))
                    os.chmod(str(units/unit.name),0o600)
        source=Path('/var/lib/jetson-bot/budget/ledger.sqlite')
        if source.exists():
            # Use the managed container's online SQLite backup API. The host's
            # Python 3.6 does not implement Connection.backup.
            run('docker','exec','jetson-budget','/opt/hermes/.venv/bin/python','-c',
                'import sqlite3; s=sqlite3.connect("/budget/ledger.sqlite"); '
                'd=sqlite3.connect("/budget/snapshot.sqlite"); s.backup(d); d.close(); s.close()')
            run('cp','/var/lib/jetson-bot/budget/snapshot.sqlite',str(target/'ledger.sqlite'))
            os.chmod(str(target/'ledger.sqlite'),0o600)
        archive=target/(time.strftime('%Y%m%d-%H%M%S')+'.tar.gz')
        partial=archive.with_suffix('.gz.partial')
        command=['tar','--exclude=state/cache','--exclude=state/logs','--exclude=node_modules',
            '--exclude=.venv','-I','gzip -1','-cf',str(partial),'-C','/srv/jetson-bot','state','workspace',
            '-C','/etc','jetson-bot','-C',str(target),'ledger.sqlite']
        for extra in ('opt/jetson-bot','opt/project-hub','var/lib/project-hub'):
            if Path('/'+extra).exists(): command.extend(['-C','/',extra])
        run(*command)
        partial.rename(archive)
        os.chmod(str(archive),0o600)
        for old in sorted(target.glob('*.tar.gz'))[:-7]: old.unlink()
        print('Backup saved: '+str(archive))
    finally:
        if running: run('systemctl','start',*running)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['status','budget','pause','resume','start','stop','configure','backup'])
    args=parser.parse_args()
    if os.geteuid()!=0: raise SystemExit('Run sudo jetsonctl '+args.command)
    if args.command=='configure': return configure()
    if args.command=='backup': return backup()
    if args.command in ('status','budget','pause','resume'):
        if args.command=='status':
            subprocess.run(['systemctl','is-active','jetson-budget.service','jetson-bot.service'])
        try: print(json.dumps(api(args.command),indent=2))
        except Exception: raise SystemExit('Budget service unavailable. Inspect systemctl status jetson-budget.')
    elif args.command=='start': run('systemctl','start','jetson-bot.service')
    elif args.command=='stop': run('systemctl','stop','jetson-bot.service')


if __name__=='__main__': main()
