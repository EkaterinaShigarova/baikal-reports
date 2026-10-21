"""SQLite full-text page index and strictly separated reviewed observations."""
import json,sqlite3
from .core import DATA,ROOT,load_sources,page_dir,read_json,validate_observation


def connect():
    DATA.mkdir(exist_ok=True)
    db=sqlite3.connect(DATA/'baikal.sqlite');db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pages(source_id TEXT,page INTEGER,sha256 TEXT,text TEXT,method TEXT,warnings TEXT,
      PRIMARY KEY(source_id,page));
    CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(source_id UNINDEXED,page UNINDEXED,text,tokenize='unicode61');
    CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS indexed(source_id TEXT PRIMARY KEY,fingerprint TEXT);
    ''')
    return db


def index_sources():
    db=connect();counts={'pages':0,'sources':0}
    with db:
        for s in load_sources()['sources']:
            db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)',(s['id'],json.dumps(s,ensure_ascii=False)))
            if not s.get('sha256'):continue
            files=sorted((page_dir(s)/'pages').glob('*.json'))
            fingerprint=s['sha256']+':'+str(len(files))+':'+str(max((p.stat().st_mtime_ns for p in files),default=0))
            old=db.execute('SELECT fingerprint FROM indexed WHERE source_id=?',(s['id'],)).fetchone()
            if old and old[0]==fingerprint:continue
            db.execute('DELETE FROM pages WHERE source_id=?',(s['id'],));db.execute('DELETE FROM page_search WHERE source_id=?',(s['id'],))
            for file in files:
                p=read_json(file)
                if p['status']!='ok':continue
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',(s['id'],p['page'],s['sha256'],p['text'],p['method'],json.dumps(p['warnings'])))
                db.execute('INSERT INTO page_search VALUES(?,?,?)',(s['id'],p['page'],p['text']))
                counts['pages']+=1
            db.execute('INSERT OR REPLACE INTO indexed VALUES(?,?)',(s['id'],fingerprint));counts['sources']+=1
        rows=read_json(ROOT/'datasets/observations.json',[])
        for row in rows:
            errors=validate_observation(row)
            if errors:raise ValueError(f'{row.get("id")}: {errors}')
        db.execute('DELETE FROM observations')
        for row in rows:db.execute('INSERT INTO observations VALUES(?,?)',(row['id'],json.dumps(row,ensure_ascii=False)))
    db.close();return counts


def search(query,limit=30):
    import re
    terms=re.findall(r'\w+',query,flags=re.UNICODE)
    if not terms:return []
    match=' AND '.join('"'+t+'"' for t in terms[:15])
    with connect() as db:
        rows=db.execute("SELECT source_id,page,snippet(page_search,2,'[',']',' … ',35) AS snippet FROM page_search WHERE page_search MATCH ? ORDER BY rank LIMIT ?",(match,min(limit,100))).fetchall()
    return [dict(r) for r in rows]


def observations():return read_json(ROOT/'datasets/observations.json',[])
