import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import local
from .domain import encode, stamp

def dumps(value): return json.dumps(value,default=encode,sort_keys=True,separators=(',',':'),allow_nan=False)
def loads(value): return json.loads(value)

class Store:
    """SQLite repository boundary; domain services do not depend on SQL dialect."""
    def __init__(self,path):
        self._transactions = local()
        self.path=str(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            if not db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone():
                db.executescript((Path(__file__).parent.parent/'migrations/001_initial.sql').read_text())
            applied={r[0] for r in db.execute('SELECT version FROM schema_migrations')}
            for migration in sorted((Path(__file__).parent.parent/'migrations').glob('*.sql')):
                version=int(migration.name.split('_')[0])
                if version not in applied:
                    db.executescript('BEGIN IMMEDIATE;\n'+migration.read_text()+'\nCOMMIT;')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db
    @contextmanager
    def transaction(self):
        active = getattr(self._transactions, 'connection', None)
        if active is not None:
            yield active
            return
        db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            self._transactions.connection = db
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            self._transactions.connection = None
            db.close()
    @staticmethod
    def get(db,table,key):
        if table not in {'accounts','broker_profiles','providers','strategies','positions'}: raise ValueError('Unknown table')
        row=db.execute('SELECT payload FROM '+table+' WHERE id=?',(key,)).fetchone()
        return loads(row['payload']) if row else None
    @staticmethod
    def put(db,table,key,payload):
        if table not in {'accounts','broker_profiles','providers','strategies'}: raise ValueError('Unknown table')
        db.execute('INSERT INTO '+table+'(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,dumps(payload)))

    @staticmethod
    def symbol_mappings(db):
        rows = db.execute("SELECT canonical_symbol,mt5_symbol FROM symbol_mappings WHERE status='ACTIVE' ORDER BY canonical_symbol,mt5_symbol")
        result = {}
        for row in rows:
            result.setdefault(row['canonical_symbol'], []).append(row['mt5_symbol'])
        return result
    @staticmethod
    def audit(db,event,payload):
        when=stamp(); body=dumps(payload)
        row=db.execute('SELECT hash FROM audit_logs ORDER BY id DESC LIMIT 1').fetchone()
        previous=row['hash'] if row else 'GENESIS'
        digest=hashlib.sha256((previous+when+event+body).encode()).hexdigest()
        db.execute('INSERT INTO audit_logs(timestamp,event,payload,previous_hash,hash) VALUES(?,?,?,?,?)',(when,event,body,previous,digest))
    @staticmethod
    def verify_audit(db):
        previous='GENESIS'
        for row in db.execute('SELECT * FROM audit_logs ORDER BY id'):
            expected=hashlib.sha256((previous+row['timestamp']+row['event']+row['payload']).encode()).hexdigest()
            if row['previous_hash']!=previous or row['hash']!=expected: return False
            previous=row['hash']
        return True
