import hashlib
import json
import sqlite3
import time
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
            deadline=time.monotonic()+5
            while True:
                try:
                    db.execute('PRAGMA journal_mode=WAL');break
                except sqlite3.OperationalError as exc:
                    if 'locked' not in str(exc).lower() or time.monotonic()>=deadline: raise
                    time.sleep(.05)
            # Lock before checking versions. executescript implicitly commits,
            # so execute complete statements individually, including triggers.
            db.execute('BEGIN IMMEDIATE')
            try:
                exists=db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
                applied={r[0] for r in db.execute('SELECT version FROM schema_migrations')} if exists else set()
                migrations=sorted((Path(__file__).parent.parent/'migrations').glob('*.sql'))
                supported={int(m.name.split('_')[0]) for m in migrations}
                if applied-supported:
                    raise ValueError('Database schema is newer than this application')
                for migration in migrations:
                    if int(migration.name.split('_')[0]) in applied: continue
                    statement=''
                    for line in migration.read_text().splitlines(keepends=True):
                        statement+=line
                        if sqlite3.complete_statement(statement):
                            db.execute(statement);statement=''
                    if statement.strip(): raise ValueError('Incomplete migration SQL')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Database integrity check failed')
                if not Store.verify_audit(db):
                    raise ValueError('Audit integrity failed; restore a verified backup before startup')
                db.commit()
            except BaseException:
                db.rollback();raise
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def copy_verified(source, destination):
        """Consistent SQLite backup/restore to a NEW path; never overwrite data."""
        source=Path(source).resolve();destination=Path(destination).resolve()
        if not source.is_file(): raise ValueError('Source database does not exist')
        if any(Path(str(destination)+suffix).exists() for suffix in ('','-wal','-shm')):
            raise ValueError('Destination must be a new database path')
        # Exclusive creation prevents concurrent writers from replacing a target.
        with destination.open('xb'): pass
        destination.chmod(0o600)
        try:
            with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(str(destination)) as dst:
                src.backup(dst)
                dst.row_factory=sqlite3.Row
                if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or dst.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Database integrity check failed')
                if not Store.verify_audit(dst): raise ValueError('Audit chain check failed')
                if {r[0] for r in dst.execute('SELECT version FROM schema_migrations')} != {1,2,3}:
                    raise ValueError('Unexpected schema versions')
            return str(destination)
        except Exception as exc:
            # Preserve the artifact for diagnosis, but never mark it usable.
            raise ValueError('Copy failed verification; destination is NOT approved for use') from exc
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
