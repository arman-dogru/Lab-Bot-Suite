"""Durable recovery metadata only: no prompts, outputs, credentials or API bodies.

SQLite transactions serialize gateway and detached cron processes. Generations
prevent a late API error from resurrecting work after /stop or a new request.
"""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class RecoveryStore:
    def __init__(self,path,clock=time.time):
        self.path,self.clock=Path(path),clock
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS routes (
              key TEXT PRIMARY KEY, sid TEXT NOT NULL, generation INTEGER NOT NULL,
              origin TEXT NOT NULL, touched REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (
              id TEXT PRIMARY KEY, generation INTEGER NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
              id TEXT PRIMARY KEY, metadata TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (
              target TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS context_restarts (
              key TEXT NOT NULL, goal_hash TEXT NOT NULL, count INTEGER NOT NULL,
              PRIMARY KEY(key,goal_hash));
            CREATE TABLE IF NOT EXISTS stopped_routes (
              key TEXT PRIMARY KEY, generation INTEGER NOT NULL);
            ''')
        os.chmod(str(self.path),0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(str(self.path),timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bind(self,key,sid,origin,new_request=False,stopped=False):
        public={name:origin[name] for name in ('platform','chat_id','thread_id','user_id','chat_type')
                if name in origin}
        with self.db() as db:
            row=db.execute('SELECT sid,generation FROM routes WHERE key=?',(key,)).fetchone()
            generation=row[1] if row else 0
            if new_request or (row and sid and row[0] and sid!=row[0]):
                generation+=1
                db.execute('DELETE FROM pending WHERE target=?',('session:'+key,))
            sid=sid or (row[0] if row else '')
            db.execute('INSERT OR REPLACE INTO routes VALUES (?,?,?,?,?)',
                       (key,sid,generation,json.dumps(public),self.clock()))
            if stopped:
                db.execute('INSERT OR REPLACE INTO stopped_routes VALUES (?,?)',(key,generation))
            elif new_request:
                db.execute('DELETE FROM stopped_routes WHERE key=?',(key,))

    def cancel(self,key):
        with self.db() as db:
            db.execute('UPDATE routes SET generation=generation+1,touched=? WHERE key=?',(self.clock(),key))
            db.execute('DELETE FROM pending WHERE target=?',('session:'+key,))
            db.execute('INSERT OR REPLACE INTO stopped_routes SELECT key,generation FROM routes WHERE key=?',(key,))

    def route(self,key):
        with self.db() as db:
            row=db.execute('SELECT sid,generation,origin FROM routes WHERE key=?',(key,)).fetchone()
            if not row: return None
            stopped=db.execute('SELECT 1 FROM stopped_routes WHERE key=? AND generation=?',(key,row[1])).fetchone()
            return dict(sid=row[0],generation=row[1],origin=json.loads(row[2]),stopped=bool(stopped))

    def context_restart(self,key,goal_hash):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO context_restarts VALUES (?,?,0)',(key,goal_hash))
            db.execute('UPDATE context_restarts SET count=count+1 WHERE key=? AND goal_hash=?',(key,goal_hash))
            return db.execute('SELECT count FROM context_restarts WHERE key=? AND goal_hash=?',(key,goal_hash)).fetchone()[0]

    def clear_context_restarts(self,key):
        with self.db() as db:
            db.execute('DELETE FROM context_restarts WHERE key=?',(key,))

    def begin(self,request_id,turn_id,sid,key='',job_id='',execution_id=''):
        if not request_id or not sid:
            return
        with self.db() as db:
            metadata={'sid':sid,'key':key,'job_id':job_id,'execution_id':execution_id}
            if not job_id:
                row=db.execute('SELECT sid,generation FROM routes WHERE key=?',(key,)).fetchone()
                if row is None or (row[0] and row[0]!=sid):
                    return
                if not row[0]:
                    db.execute('UPDATE routes SET sid=? WHERE key=?',(sid,key))
                turn=sid+':'+str(turn_id or request_id)
                db.execute('INSERT OR IGNORE INTO turns VALUES (?,?,?)',(turn,row[1],self.clock()))
                metadata['generation']=db.execute('SELECT generation FROM turns WHERE id=?',(turn,)).fetchone()[0]
            db.execute('INSERT OR REPLACE INTO requests VALUES (?,?,?)',(request_id,json.dumps(metadata),self.clock()))
            db.execute('DELETE FROM requests WHERE created<?',(self.clock()-86400,))
            db.execute('DELETE FROM turns WHERE created<?',(self.clock()-7*86400,))

    def finish(self,request_id):
        with self.db() as db:
            db.execute('DELETE FROM requests WHERE id=?',(request_id,))

    def _defer(self,db,metadata,budget,why):
        key,job_id=metadata.get('key',''),metadata.get('job_id','')
        if not job_id:
            route=db.execute('SELECT sid,generation,origin FROM routes WHERE key=?',(key,)).fetchone()
            if not route or route[0]!=metadata['sid'] or route[1]!=metadata['generation']:
                return None
            if db.execute('SELECT 1 FROM stopped_routes WHERE key=? AND generation=?',(key,route[1])).fetchone():
                return None
            metadata=dict(metadata,origin=json.loads(route[2]))
        target=('cron:'+job_id) if job_id else ('session:'+key)
        row=db.execute('SELECT record FROM pending WHERE target=?',(target,)).fetchone()
        if row:
            old=json.loads(row[0])
            if old['sid']==metadata['sid'] and old['day']==budget['day'] and old['why']==why:
                return old
        record=dict(metadata,target=target,token=uuid.uuid4().hex,day=budget['day'],why=why,
                    mode='cron' if job_id else 'plain',phase='waiting',attempts=0,
                    next_try=0,created=self.clock())
        db.execute('INSERT OR REPLACE INTO pending VALUES (?,?,?)',(target,record['token'],json.dumps(record)))
        return record

    def defer_error(self,request_id,budget,why):
        with self.db() as db:
            row=db.execute('SELECT metadata FROM requests WHERE id=?',(request_id,)).fetchone()
            if row is None:
                return None
            db.execute('DELETE FROM requests WHERE id=?',(request_id,))
            return self._defer(db,json.loads(row[0]),budget,why)

    def defer_goal(self,key,sid,budget,why):
        with self.db() as db:
            route=db.execute('SELECT sid,generation FROM routes WHERE key=?',(key,)).fetchone()
            if not route or route[0]!=sid:
                return None
            return self._defer(db,{'sid':sid,'key':key,'generation':route[1],'job_id':''},budget,why)

    def defer_restart(self,key,sid,generation,budget):
        with self.db() as db:
            return self._defer(db,{'sid':sid,'key':key,'generation':generation,'job_id':''},
                               budget,'restart_interrupted')

    def pending(self):
        with self.db() as db:
            return sorted((json.loads(row[0]) for row in db.execute('SELECT record FROM pending')),
                          key=lambda record:record['created'])

    def get(self,token):
        with self.db() as db:
            row=db.execute('SELECT record FROM pending WHERE token=?',(token,)).fetchone()
            return json.loads(row[0]) if row else None

    def update(self,record,**updates):
        with self.db() as db:
            row=db.execute('SELECT record FROM pending WHERE token=?',(record['token'],)).fetchone()
            if not row:
                return None
            current=json.loads(row[0])
            current.update(updates)
            db.execute('UPDATE pending SET record=? WHERE token=?',(json.dumps(current),record['token']))
            return current

    def remove(self,token):
        with self.db() as db:
            db.execute('DELETE FROM pending WHERE token=?',(token,))

    def eligible(self,record,budget):
        if not budget or budget.get('paused') or record['phase']=='blocked' or self.clock()<record['next_try']:
            return False
        maximum=budget.get('max_request_reservation_usd')
        if not isinstance(maximum,(int,float)) or maximum<=0 or budget.get('remaining_usd',0)<maximum:
            return False
        return record['why']!='budget_exhausted' or budget['day']!=record['day']
