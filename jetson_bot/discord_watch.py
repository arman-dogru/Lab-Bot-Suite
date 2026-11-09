"""Quiet Discord dashboards from observed runtime facts; Python 3.6 compatible.

Never forwards prompts, model text, reasoning, tool arguments, or tool output.
The only credential used is the existing Discord bot token. No inference calls.
"""
import hashlib
import json
import os
import re
import subprocess
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(str(temporary), 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))


def env_value(path, key):
    prefix = key + '='
    for line in Path(path).read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise ValueError('Required service credential is missing')


class Tracker:
    def __init__(self, state, clock=time.time, ignored_chat_ids=()):
        self.state, self.clock = state, clock
        self.ignored_chat_ids = {str(value) for value in ignored_chat_ids}
        self.state.setdefault('tasks', [])
        self.state.setdefault('sequence', 0)

    @property
    def current(self):
        tasks = self.state['tasks']
        return tasks[-1] if tasks else None

    def consume(self, line):
        entry = re.match(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ '
                         r'(?:INFO|WARNING|ERROR|DEBUG) (?:\[[^\]\n]+\] )?([\w.]+): (.*)$', line)
        if not entry:
            return
        logger, body = entry.groups()
        incoming = (re.match(r'inbound message: platform=discord .*?chat=(\d+)', body)
                    if logger == 'gateway.run' else None)
        if incoming:
            if incoming.group(1) in self.ignored_chat_ids:
                return
            if "msg='[Jetson budget resume " in body:
                prior=next((task for task in reversed(self.state['tasks'])
                            if task['chat_id']==incoming.group(1) and task['status']=='Budget paused'),None)
                if prior:
                    self.state['tasks'].remove(prior)
                    self.state['tasks'].append(prior)
                    prior.update(status='Running',turn_calls=0,budget_paused=False)
                    return
            current = self.current
            if (current and current['chat_id']==incoming.group(1)
                    and "msg='[Continuing toward your standing goal" in body):
                current.update(status='Running',turn_calls=0)
                return
            if current and current['status'] == 'Running':
                current['status'] = 'Interrupted'
            self.state['sequence'] += 1
            self.state['tasks'].append(dict(
                number=self.state['sequence'], nonce=uuid.uuid4().hex[:20],
                chat_id=incoming.group(1), status='Running', started=self.clock(),
                tools={}, failures=0, calls=0, turn_calls=0, output_tokens=0, duration=None,
            ))
            self.state['tasks'] = self.state['tasks'][-30:]
            retained = {'status'} | {'activity:' + task['nonce'] for task in self.state['tasks']}
            for slot in list(self.state.get('cards', {})):
                if slot not in retained:
                    del self.state['cards'][slot]
            return
        task = self.current
        if task and logger=='hermes_cli.goals' and body.startswith('goal judge: verdict='):
            verdict=body.split('verdict=',1)[1].split()[0]
            if verdict in ('continue','wait'):
                task['status']='Continuing' if verdict=='continue' else 'Waiting'
            elif verdict=='done':
                task['status']='Finished'
            elif verdict=='blocked':
                task['status']='Blocked'
            return
        if task and logger=='jetson.goal_guard':
            task['status']='Blocked'
            return
        if not task or task['status'] != 'Running':
            return
        if (logger=='agent.conversation_loop' and 'HTTP 402:' in body
                and ('Paid work is paused' in body or 'Daily API budget' in body)):
            task['budget_paused']=True
        tool = (re.match(r'tool ([A-Za-z0-9_-]+) (completed|failed) \(', body)
                if logger == 'agent.tool_executor' else None)
        tool_error = (re.match(r'Tool ([A-Za-z0-9_-]+) returned error \(', body)
                      if logger == 'agent.tool_executor' else None)
        if tool or tool_error:
            name = (tool or tool_error).group(1)
            task['tools'][name] = task['tools'].get(name, 0) + 1
            task['failures'] += int(bool(tool_error) or tool.group(2) == 'failed')
        api = (re.match(r'API call #(\d+): .*?out=(\d+)\b', body)
               if logger == 'agent.conversation_loop' else None)
        if api:
            task['calls'] += 1
            task['turn_calls'] = task.get('turn_calls',0)+1
            task['output_tokens'] += int(api.group(2))
        finished = (re.match(
            r'response ready: platform=discord chat=(\d+).*?time=([\d.]+)s api_calls=(\d+)', body)
            if logger == 'gateway.run' else None)
        if finished and finished.group(1) == task['chat_id']:
            task['calls'] += max(0,int(finished.group(3))-task.get('turn_calls',0))
            task.update(status='Budget paused' if task.get('budget_paused') else 'Finished',
                        duration=(task.get('duration') or 0)+float(finished.group(2)))
        elif logger == 'gateway.run' and body.startswith('Stopping gateway...'):
            task['status'] = 'Interrupted'
        elif logger == 'gateway.run' and body.startswith('Invalidated run generation') and '(stop' in body:
            task['status'] = 'Stopped'


TOOL_LABELS = {
    'terminal': 'Commands', 'read_file': 'Files read', 'write_file': 'Files written',
    'patch': 'Patches', 'skill_view': 'Skills loaded', 'skill_manage': 'Skills saved',
    'memory': 'Memory updates', 'web_search': 'Web searches', 'web_extract': 'Pages read',
    'search_files': 'File searches', 'cronjob': 'Schedules', 'cronjob_manage': 'Schedules',
}


def activity_text(task, server_id):
    lines = ['**Task activity #{} · {}**'.format(task['number'], task['status']),
             '[Open task conversation](https://discord.com/channels/{}/{})'.format(server_id, task['chat_id']),
             'Started: ' + time.strftime('%b %d, %H:%M %Z', time.localtime(task['started']))]
    if task.get('duration') is not None:
        lines.append('Run duration: {:.1f}s'.format(task['duration']))
    lines.append('Main model calls: {}; output tokens: {}'.format(task['calls'], task['output_tokens']))
    lines.append('Observed tool operations: {}'.format(sum(task['tools'].values())))
    for name, count in sorted(task['tools'].items()):
        lines.append('• {}: {}'.format(TOOL_LABELS.get(name, name), count))
    if task['failures']:
        lines.append('Tool errors: {}'.format(task['failures']))
    lines.append('Activity facts only; narration and private reasoning are hidden. Helper spending is included in the budget panel.')
    return '\n'.join(lines)


def status_text(agent_online, budget, task, public, recovery=None):
    state = 'Service running' if agent_online else 'Offline / restarting'
    if agent_online:
        state += ' · working' if task and task['status'] == 'Running' else ' · idle'
    lines = ['**Jetson Bot · Status**', state]
    if budget is None:
        lines.append('Budget gateway unavailable; paid work is blocked.')
    else:
        lines.extend([
            '{} · {}'.format(budget['day'], budget['timezone']),
            'Billed today: ${:.4f} / ${:.2f}'.format(budget['billed_usd'], budget['cap_usd']),
            'Available after reservations: ${:.4f}'.format(budget['remaining_usd']),
            'Outstanding reservations: {}'.format(budget['outstanding_reservations']),
            'Paid work: ' + ('paused by operator' if budget['paused'] else 'enabled'),
        ])
    if recovery is not None:
        lines.append('Saved budget-paused work: {}; needing attention: {}'.format(
            recovery['waiting'],recovery['blocked']))
    lines.extend([
        'Chat and files: <#{}>'.format(public['channel_id']),
        'Task activity: <#{}>'.format(public['activity_channel_id']),
        'Local controls: `/jetson status`, `/jetson budget`, `/jetson pause`, `/jetson resume`, `/jetson jobs`.',
        'This panel uses no model calls. It is edited when state changes.',
    ])
    return '\n'.join(lines)


class Discord:
    def __init__(self, token, cards, request=None, clock=time.time, persist=None):
        self.token, self.cards, self.clock = token, cards, clock
        self.request = request or self._request
        self.persist = persist or (lambda: None)
        self.retry_at = 0

    def _request(self, method, path, payload):
        request = Request('https://discord.com/api/v10' + path, method=method,
                          data=json.dumps(payload).encode(), headers={
                              'Authorization': 'Bot ' + self.token,
                              'Content-Type': 'application/json', 'User-Agent': 'JetsonBot/1.0'})
        with urlopen(request, timeout=8) as response:
            return json.load(response)

    def upsert(self, slot, channel, text, minimum_interval=15):
        now = self.clock()
        card = self.cards.setdefault(slot, {})
        if (card.get('text') == text or now < self.retry_at
                or now - card.get('updated', -1000000) < minimum_interval):
            return False
        payload = {'content': text[:1900], 'allowed_mentions': {'parse': []}}
        message_id = card.get('message_id')
        method = 'PATCH' if message_id else 'POST'
        path = '/channels/{}/messages'.format(channel)
        if message_id:
            path += '/' + message_id
        else:
            payload.update(nonce=hashlib.sha256(slot.encode()).hexdigest()[:24], enforce_nonce=True, flags=4096)
        try:
            result = self.request(method, path, payload)
        except HTTPError as error:
            code = error.code
            if code == 429:
                try:
                    delay = max(5, float(json.load(error).get('retry_after', 30)))
                except Exception:
                    delay = 30
                self.retry_at = now + delay
            elif code == 404 and message_id:
                card.pop('message_id', None)
            else:
                self.retry_at = now + 60
            error.close()
            return False
        except Exception:
            self.retry_at = now + 30
            return False
        card.update(message_id=str(result['id']), text=text, updated=now)
        self.persist()
        return True


def budget_snapshot(token):
    request = Request('http://127.0.0.1:8870/budget', headers={'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    except Exception:
        return None


def recovery_snapshot(path):
    if not path.exists(): return None
    try:
        db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=0.2)
        try:
            phases=[json.loads(row[0]).get('phase') for row in db.execute('SELECT record FROM pending')]
        finally:
            db.close()
        return {'waiting':sum(phase!='blocked' for phase in phases),'blocked':phases.count('blocked')}
    except (sqlite3.Error,ValueError):
        return None


def read_new_lines(path, state):
    try:
        info = path.stat()
    except FileNotFoundError:
        return []
    cursor = state.setdefault('cursor', {})
    if not cursor:
        # On first install, start now rather than flooding Discord with old test history.
        cursor.update(inode=info.st_ino, offset=info.st_size)
        return []
    offset = cursor.get('offset', 0)
    if cursor.get('inode') != info.st_ino or offset > info.st_size:
        offset = 0
    with path.open('rb') as stream:
        stream.seek(offset)
        chunk = stream.read(1024 * 1024)
    # An incomplete write is consumed on the next pass, never parsed twice.
    end = chunk.rfind(b'\n') + 1
    cursor.update(inode=info.st_ino, offset=offset + end)
    return chunk[:end].decode('utf-8', errors='replace').splitlines()


def main():
    os.umask(0o077)
    home = Path('/srv/jetson-bot/state')
    public = json.loads((home / 'discord-channels.json').read_text())
    for key in ('status_channel_id', 'activity_channel_id'):
        if not str(public.get(key, '')).isdigit():
            raise SystemExit('Separate Discord channels are not configured')
    state_path = home / 'discord-watch' / 'state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    tracker = Tracker(state, ignored_chat_ids=(public['status_channel_id'],public['activity_channel_id']))
    cards = state.setdefault('cards', {})
    last_saved = [None]
    def persist():
        encoded = json.dumps(state, sort_keys=True)
        if encoded != last_saved[0]:
            save_json(state_path, state)
            last_saved[0] = encoded
    discord = Discord(env_value(home / 'runtime.env', 'DISCORD_BOT_TOKEN'), cards, persist=persist)
    log = Path('/srv/jetson-bot/state/logs/agent.log')
    while True:
        for line in read_new_lines(log, state):
            tracker.consume(line)
        online = subprocess.run(['systemctl', 'is-active', '--quiet', 'jetson-bot.service'],
                                timeout=5).returncode == 0
        if not online and tracker.current and tracker.current['status'] == 'Running':
            tracker.current['status'] = 'Interrupted'
        budget = budget_snapshot(env_value(home / 'runtime.env', 'JETSON_LOCAL_TOKEN'))
        discord.upsert('status', public['status_channel_id'], status_text(
            online,budget,tracker.current,public,recovery_snapshot(home/'jetson-recovery.sqlite')))
        for task in state['tasks']:
            discord.upsert('activity:' + task['nonce'], public['activity_channel_id'],
                           activity_text(task, public['server_id']))
        persist()
        time.sleep(5)


if __name__ == '__main__':
    main()
