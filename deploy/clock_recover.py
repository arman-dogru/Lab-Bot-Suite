#!/usr/bin/python3
"""Boot time recovery for Nano boards without a working battery-backed RTC.

Only this credential-free HEAD bootstrap can ignore certificate time windows.
Certificate-chain signatures and hostnames still must verify. Application TLS
settings are never changed. Two independent HTTPS Date values must agree.
Compatible with the host's Python 3.6.
"""
import email.utils
import json
import os
import re
import subprocess
import time
from pathlib import Path

SOURCES=("github.com","www.google.com","www.cloudflare.com")
FLOOR=1793232000  # 2026-10-29; deployment's independently known date
STATE=Path('/var/lib/jetson-bot/last-good-time')
VERIFIED=Path('/run/jetson-clock-verified')
TIMESYNC=Path('/var/lib/systemd/timesync/clock')


def source_time(host):
    request=('HEAD / HTTP/1.1\r\nHost: '+host+'\r\nConnection: close\r\n\r\n').encode()
    args=['openssl','s_client','-connect',host+':443','-servername',host,
          '-verify_hostname',host,'-verify_return_error','-no_check_time',
          '-CApath','/etc/ssl/certs','-quiet']
    result=subprocess.run(args,input=request,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=18)
    if result.returncode:
        raise ValueError('Certificate-chain/hostname check failed for '+host)
    match=re.search(br'(?im)^date:\s*([^\r\n]+)',result.stdout)
    if not match: raise ValueError('No HTTPS date from '+host)
    return email.utils.parsedate_to_datetime(match.group(1).decode()).timestamp()


def main():
    STATE.parent.mkdir(parents=True,exist_ok=True)
    floor=max(FLOOR,float(STATE.read_text()) if STATE.exists() else FLOOR)
    if time.time()<floor:
        subprocess.run(['date','-u','-s','@'+str(int(floor))],check=True,stdout=subprocess.DEVNULL)
    samples=[]
    for host in SOURCES:
        try:
            value=source_time(host)
            if value>=floor-300 and value<floor+10*366*86400:
                samples.append(value)
        except Exception:
            continue
        if len(samples)>=2 and abs(samples[-1]-samples[-2])<120:
            target=int(max(samples[-2:]))
            subprocess.run(['date','-u','-s','@'+str(target)],check=True,stdout=subprocess.DEVNULL)
            STATE.write_text(str(target))
            # /run is cleared at boot: a persisted time floor alone cannot prove
            # today's date for budget accounting around Toronto midnight.
            if not VERIFIED.exists(): VERIFIED.write_text(str(target))
            clock=TIMESYNC
            if clock.exists(): os.utime(str(clock),(target,target))
            print('Clock recovered from two independently verified HTTPS hosts.')
            return
    raise SystemExit('Clock recovery could not establish agreement; retained last-good time floor.')


if __name__=='__main__': main()
