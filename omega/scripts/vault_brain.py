#!/usr/bin/env python3
"""OMEGA Vault Brain: local, stdlib-only FTS5 + links + explicit memory lifecycle.

This is an Obsidian-compatible offline bridge, NOT GBrain itself or an MCP server.
No network operations and no implicit capture.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

WIKI = re.compile(r'\[\[([^\]]+)\]\]')
EXCLUDED = {'.obsidian', '.git', '.omega-state', 'node_modules', '.trash', '09_Archive'}
DATE = lambda: dt.datetime.now(dt.timezone.utc).date().isoformat()


def split_frontmatter(text):
    if not text.startswith('---\n'):
        return {}, text
    end = text.find('\n---\n', 4)
    if end < 0:
        return {}, text
    meta = {}
    for ln in text[4:end].splitlines():
        if ':' in ln and not ln.startswith((' ', '\t', '#')):
            key, value = ln.split(':', 1)
            meta[key.strip()] = value.strip().strip('"\'')
    return meta, text[end + 5:]


def read_note(path):
    content = path.read_text(encoding='utf-8-sig')
    metadata, body = split_frontmatter(content)
    return metadata, body, content


def get_db(db):
    p = Path(db).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('CREATE TABLE IF NOT EXISTS pages (path TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT, entity TEXT, source TEXT, verification TEXT, visibility TEXT, updated TEXT, sha256 TEXT)')
    con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS searchable USING fts5(path UNINDEXED, title, body, tokenize="unicode61")')
    con.execute('CREATE TABLE IF NOT EXISTS edges (src TEXT NOT NULL, dst TEXT NOT NULL, PRIMARY KEY(src,dst))')
    return con


def notes_in(vault):
    for path in Path(vault).rglob('*.md'):
        relparts = path.relative_to(vault).parts
        if any(part in EXCLUDED or part.startswith('.') for part in relparts):
            continue
        if path.is_file() and path.stat().st_size <= 2_000_000:
            yield path


def scan(vault, db):
    vault = Path(vault).expanduser().resolve()
    if not vault.is_dir():
        raise ValueError(f'Vault does not exist: {vault}')
    con = get_db(db)
    seen = set()
    for path in notes_in(vault):
        rel = path.relative_to(vault).as_posix()
        seen.add(rel)
        metadata, body, raw = read_note(path)
        sha = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        prev = con.execute('SELECT sha256 FROM pages WHERE path=?', (rel,)).fetchone()
        if prev and prev[0] == sha:
            continue
        title = next((x[2:].strip() for x in body.splitlines() if x.startswith('# ')), path.stem)
        con.execute('REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?,?,?)', (rel, title, body, metadata.get('status','active'), metadata.get('entity',''), metadata.get('source_url', metadata.get('provenance','')), metadata.get('verification','unverified'), metadata.get('visibility','private'), metadata.get('updated',''), sha))
        con.execute('DELETE FROM searchable WHERE path=?', (rel,))
        con.execute('INSERT INTO searchable(path,title,body) VALUES (?,?,?)',(rel,title,body))
        con.execute('DELETE FROM edges WHERE src=?',(rel,))
        for target in WIKI.findall(body):
            target = target.split('|',1)[0].split('#',1)[0].strip()
            if target:
                con.execute('INSERT OR IGNORE INTO edges VALUES (?,?)',(rel,target))
    deleted = [x[0] for x in con.execute('SELECT path FROM pages') if x[0] not in seen]
    for rel in deleted:
        con.execute('DELETE FROM pages WHERE path=?',(rel,))
        con.execute('DELETE FROM searchable WHERE path=?',(rel,))
        con.execute('DELETE FROM edges WHERE src=?',(rel,))
    con.commit()
    count = con.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
    print(json.dumps({'indexed':count,'removed':len(deleted),'db':str(Path(db).expanduser().resolve())},ensure_ascii=False))


def safe_query(q):
    # Phrase query prevents exposing raw FTS operators and tolerates punctuation.
    return '"' + q.strip().replace('"','""') + '"'


def search(db, text, limit):
    con = get_db(db)
    if not text.strip():
        raise ValueError('Empty query')
    rows = con.execute('''SELECT p.path,p.title,p.status,p.verification,p.source,
       snippet(searchable,2,'[',']',' … ',18) FROM searchable
       JOIN pages p ON p.path=searchable.path
       WHERE searchable MATCH ? AND p.status='active'
       ORDER BY bm25(searchable) LIMIT ?''',(safe_query(text),limit)).fetchall()
    if not rows:  # CJK and substring fallback; FTS5 unicode61 is not a Japanese segmenter.
        escaped=text.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        rows = [(path,title,status,verified,source,body[:220]) for path,title,status,verified,source,body in con.execute(
            "SELECT path,title,status,verification,source,body FROM pages WHERE status='active' AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') LIMIT ?",
            ('%'+escaped+'%','%'+escaped+'%',limit))]
    print(json.dumps([dict(zip(['path','title','status','verification','source','snippet'],row)) for row in rows],ensure_ascii=False,indent=2))


def audit(db):
    con = get_db(db)
    pages = [p[0] for p in con.execute('SELECT path FROM pages')]
    titles = {p.removesuffix('.md') for p in pages}
    stems = {Path(p).stem for p in pages}
    bad = []
    for src,dst in con.execute('SELECT src,dst FROM edges'):
        normalized=dst.removesuffix('.md')
        if normalized not in titles and normalized not in stems:
            bad.append({'source':src,'unresolved':dst})
    print(json.dumps({'pages':len(pages),'edges':con.execute('SELECT COUNT(*) FROM edges').fetchone()[0], 'unresolved':bad},ensure_ascii=False,indent=2))


def yaml_atom(s):
    return json.dumps(str(s),ensure_ascii=False)


def write_atomic(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix='.omega-',dir=path.parent)
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as f:
            f.write(value)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def remember(vault,entity,claim,provenance,announce=True):
    if not (entity.strip() and claim.strip() and provenance.strip()):
        raise ValueError('Entity, claim and provenance required')
    vault=Path(vault).expanduser().resolve()
    if not vault.is_dir(): raise ValueError('Vault missing')
    digest=hashlib.sha256((entity+'\n'+claim).encode()).hexdigest()[:12]
    stamp=DATE()
    target=vault/'03_Knowledge'/'Memory'/f'fact-{stamp}-{digest}.md'
    if target.exists(): raise FileExistsError('Existing fact; avoid duplicate write')
    doc=f'''---\ntype: memory-fact\nfact_id: {yaml_atom(digest)}\nentity: {yaml_atom(entity)}\nstatus: active\nverification: user-confirmed\nvisibility: private\ncreated: {stamp}\nupdated: {stamp}\nprovenance: {yaml_atom(provenance)}\n---\n# Memory — {entity}\n\n{claim}\n'''
    write_atomic(target,doc)
    if announce: print(json.dumps({'created':target.relative_to(vault).as_posix(),'fact_id':digest},ensure_ascii=False))


def transition(vault,rel,new_status,replacement=None,provenance=None):
    vault=Path(vault).expanduser().resolve()
    target=(vault/rel).resolve()
    if not target.is_relative_to(vault) or not target.is_file() or 'Memory' not in target.parts:
        raise ValueError('Only existing fact notes inside vault/Memory can change')
    metadata,body,raw=read_note(target)
    if metadata.get('type')!='memory-fact' or metadata.get('status')!='active':
        raise ValueError('Only active memory-fact notes can transition')
    if not raw.startswith('---\n'):
        raise ValueError('Missing metadata')
    updated=raw.replace('status: active',f'status: {new_status}',1)
    updated=re.sub(r'^updated:.*$', f'updated: {DATE()}',updated,count=1,flags=re.MULTILINE)
    if replacement is not None:
        if not provenance: raise ValueError('Correction must include provenance')
        remember(vault, metadata.get('entity','unknown'),replacement,provenance,announce=False)
    # Safely keep old claim, but remove it from active search after reindex.
    write_atomic(target,updated)
    print(json.dumps({'updated':str(target.relative_to(vault)),'status':new_status},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='~/.omega/brain.sqlite',help='Local SQLite outside the synchronized vault')
    sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('index');s.add_argument('--vault',required=True)
    s=sub.add_parser('search');s.add_argument('query');s.add_argument('--limit',type=int,default=10)
    sub.add_parser('audit')
    s=sub.add_parser('remember');s.add_argument('--vault',required=True);s.add_argument('--entity',required=True);s.add_argument('--claim',required=True);s.add_argument('--provenance',required=True)
    s=sub.add_parser('correct');s.add_argument('--vault',required=True);s.add_argument('--path',required=True);s.add_argument('--claim',required=True);s.add_argument('--provenance',required=True)
    s=sub.add_parser('withdraw');s.add_argument('--vault',required=True);s.add_argument('--path',required=True)
    a=p.parse_args()
    try:
        if a.cmd=='index':scan(a.vault,a.db)
        if a.cmd=='search':search(a.db,a.query,a.limit)
        if a.cmd=='audit':audit(a.db)
        if a.cmd=='remember':remember(a.vault,a.entity,a.claim,a.provenance)
        if a.cmd=='correct':transition(a.vault,a.path,'superseded',a.claim,a.provenance)
        if a.cmd=='withdraw':transition(a.vault,a.path,'withdrawn')
    except (ValueError,FileExistsError,sqlite3.Error,OSError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)

if __name__=='__main__':main()
