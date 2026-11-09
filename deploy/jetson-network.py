#!/usr/bin/python3
"""Headless NetworkManager setup and optional Windows USB sharing (Python 3.6)."""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

STATE = Path('/var/lib/jetson-bot/network')
SHARING = STATE / 'usb-sharing-enabled'
USB = 'l4tbr0'
USB_ADDRESS = '192.168.137.2/24'
GATEWAY = '192.168.137.1'


def output(*args):
    return subprocess.check_output(args, universal_newlines=True).strip()


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def profile_value(uuid, key):
    return output('nmcli', '-g', key, 'connection', 'show', 'uuid', uuid)


def configure_known():
    """Idempotent: a recovery tick must not rewrite or bounce a healthy profile."""
    rows = output('nmcli', '-t', '-f', 'UUID,TYPE', 'connection', 'show')
    configured = []
    desired = [('connection.permissions', ''), ('connection.autoconnect', 'yes'),
               ('connection.autoconnect-retries', '3'), ('802-11-wireless.powersave', '2')]
    for row in rows.splitlines():
        uuid, kind = row.split(':', 1)
        if kind != '802-11-wireless':
            continue
        changes = []
        for key, value in desired:
            current = profile_value(uuid, key)
            # Ubuntu 18.04 nmcli renders this enum as a name even with -g.
            if key == '802-11-wireless.powersave' and current == 'disable':
                current = '2'
            if current != value:
                changes.extend([key, value])
        if changes:
            run('nmcli', 'connection', 'modify', 'uuid', uuid, *changes)
        configured.append(uuid)
    return configured


def backup_profiles():
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(str(STATE), 0o700)
    target = STATE / 'original-profiles'
    if not target.exists():
        shutil.copytree('/etc/NetworkManager/system-connections', str(target))
        os.chmod(str(target), 0o700)
        for path in target.iterdir():
            if path.is_file():
                os.chmod(str(path), 0o600)


def prepare_usb():
    addresses = json.loads(output('ip', '-j', '-4', 'address', 'show'))
    if not any(row.get('ifname') == USB for row in addresses):
        raise RuntimeError('Expected Jetson USB bridge is missing.')
    for row in addresses:
        for address in row.get('addr_info', []):
            if address.get('local', '').startswith('192.168.137.') and row['ifname'] != USB:
                raise RuntimeError('USB sharing subnet conflicts with another interface.')
    own = next(row for row in addresses if row['ifname'] == USB)
    if not any(a.get('local') == '192.168.137.2' for a in own.get('addr_info', [])):
        run('ip', 'address', 'add', USB_ADDRESS, 'dev', USB)


def remove_share_route():
    routes = output('ip', '-4', 'route', 'show', 'default')
    if any('via ' + GATEWAY + ' dev ' + USB in line and 'metric 1500' in line
           for line in routes.splitlines()):
        run('ip', 'route', 'del', 'default', 'via', GATEWAY, 'dev', USB, 'metric', '1500')
        run('systemd-resolve', '--interface=' + USB, '--revert')


def recover_sharing():
    if not SHARING.exists():
        return False
    prepare_usb()
    # Check the laptop's DNS proxy before installing an internet route.
    if not sharing_gateway_ready():
        remove_share_route()
        return False
    run('ip', 'route', 'replace', 'default', 'via', GATEWAY, 'dev', USB, 'metric', '1500')
    run('systemd-resolve', '--interface=' + USB, '--set-dns=' + GATEWAY)
    return True


def sharing_gateway_ready():
    identifier = os.urandom(2)
    query = identifier + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + b'\x07discord\x03com\x00\x00\x01\x00\x01'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.bind(('192.168.137.2', 0))
            client.sendto(query, (GATEWAY, 53))
            reply, sender = client.recvfrom(4096)
        return (sender == (GATEWAY, 53) and len(reply) >= 12 and reply[:2] == identifier
                and reply[2] & 128 != 0 and reply[3] & 15 == 0
                and int.from_bytes(reply[6:8], 'big') > 0)
    except OSError:
        return False


def recover():
    configure_known()
    run('nmcli', 'radio', 'wifi', 'on')
    run('nmcli', 'device', 'set', 'wlan0', 'autoconnect', 'yes')
    recover_sharing()
    print('Known Wi-Fi profiles available for unattended connection.')


def decode_ssid(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if not 1 <= len(raw) <= 32 or b'\0' in raw or b'\n' in raw or b'\r' in raw:
        raise ValueError('SSID must contain 1 to 32 bytes, without control separators.')
    return raw.decode('utf-8')


def connect_wifi(encoded):
    ssid = decode_ssid(encoded)
    backup_profiles()
    configure_known()
    run('nmcli', 'radio', 'wifi', 'on')
    # nmcli's terminal prompt hides the secret; it is never a process argument.
    run('nmcli', '--ask', '--wait', '60', 'device', 'wifi', 'connect', ssid)
    configure_known()
    uuid = output('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', 'wlan0')
    run('nmcli', 'connection', 'modify', 'uuid', uuid, 'connection.autoconnect-priority', '100')
    if SHARING.exists():
        SHARING.unlink()
    remove_share_route()
    print('Wi-Fi saved for automatic use after boot; USB management remains available.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('recover', 'wifi', 'share-prepare', 'share-enable', 'share-disable', 'scan'))
    parser.add_argument('ssid_base64', nargs='?')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run through sudo.')
    if args.action == 'wifi':
        if not args.ssid_base64:
            parser.error('An encoded SSID is required.')
        connect_wifi(args.ssid_base64)
    elif args.action == 'scan':
        run('nmcli', '-f', 'IN-USE,SSID,SECURITY,SIGNAL', 'device', 'wifi', 'list', '--rescan', 'yes')
    elif args.action == 'share-prepare':
        prepare_usb()
        print('USB sharing address prepared; original 192.168.55.1 address preserved.')
    elif args.action == 'share-enable':
        STATE.mkdir(parents=True, exist_ok=True)
        os.chmod(str(STATE), 0o700)
        SHARING.touch(mode=0o600)
        if not recover_sharing():
            raise SystemExit('Laptop sharing gateway is not ready; recovery will retry automatically.')
        print('USB internet fallback enabled; saved Wi-Fi remains preferred.')
    elif args.action == 'share-disable':
        if SHARING.exists():
            SHARING.unlink()
        remove_share_route()
        print('Jetson USB internet fallback disabled.')
    else:
        backup_profiles()
        recover()


if __name__ == '__main__':
    main()
