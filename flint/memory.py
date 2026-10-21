"""Bounded, source-linked durable memories. No model or external service.

BM25 is a standard retrieval algorithm, not a semantic understanding claim.
Current recall excludes superseded and changed-source memories. History is
explicit; forgetting deletes this key's stored payloads, not the source file.
"""
from collections import Counter
import json
import math
import re

from .common import FlintError, digest

MAX_MEMORY = 65536
MAX_TOTAL = 4*1024*1024
MAX_CURRENT = 128
MAX_VERSIONS = 512
MAX_INDEX_BYTES = 512*1024


def terms(text):
    return re.findall(r'[^\W_]+',text.casefold(),re.UNICODE)


def render_recall(result):
    header = f'MEMORY/1 hits={len(result["hits"])} omitted={result["omitted"]} stale={result["stale"]}\n'
    return header + ''.join(f'MEM {r["id"]} key={json.dumps(r["key"],ensure_ascii=False)} bytes={len(r["content"].encode())}\n'+r['content']+'\n' for r in result['hits'])


class Memory:
    def __init__(self, store):
        self.store=store; self.db=store.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS memory_versions(
                id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
                source TEXT NOT NULL, hash TEXT NOT NULL, content BLOB NOT NULL,
                previous INTEGER, current INTEGER NOT NULL CHECK(current IN (0,1)),
                tracked INTEGER NOT NULL CHECK(tracked IN (0,1)));
            CREATE UNIQUE INDEX IF NOT EXISTS memory_current_key ON memory_versions(key) WHERE current=1;
        ''')

    def remember(self, path, key, *, tracked=False):
        if not isinstance(key,str) or not key.strip() or len(key)>256:
            raise FlintError('memory key must contain 1..256 characters')
        data=self.store.file_bytes(path)
        if not data or len(data)>MAX_MEMORY:
            raise FlintError('memory source must contain 1..65536 bytes')
        try:data.decode('utf-8')
        except UnicodeError as error:raise FlintError('memory must be UTF-8') from error
        handle=digest(data)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT id,hash,source,content,tracked FROM memory_versions WHERE key=? AND current=1',(key,)).fetchone()
            if old and old[1]==handle and old[2]==path and old[4]==int(tracked):
                if bytes(old[3])!=data:raise FlintError('memory integrity failure')
                return 'm'+str(old[0])
            count,total=self.db.execute('SELECT count(*),coalesce(sum(length(content)),0) FROM memory_versions').fetchone()
            active=self.db.execute('SELECT count(*) FROM memory_versions WHERE current=1').fetchone()[0]
            active_bytes=self.db.execute('SELECT coalesce(sum(length(content)),0) FROM memory_versions WHERE current=1').fetchone()[0]
            if active_bytes+len(data)-(len(old[3]) if old else 0)>MAX_INDEX_BYTES:
                raise FlintError('current memory index exceeds 512 KiB')
            if count>=MAX_VERSIONS or total+len(data)>MAX_TOTAL or (old is None and active>=MAX_CURRENT):
                raise FlintError('memory storage budget exhausted')
            if old:self.db.execute('UPDATE memory_versions SET current=0 WHERE id=?',(old[0],))
            row=self.db.execute('INSERT INTO memory_versions(key,source,hash,content,previous,current,tracked) VALUES(?,?,?,?,?,1,?)',
                                (key,path,handle,data,old[0] if old else None,int(tracked)))
            return 'm'+str(row.lastrowid)

    def recover(self, reference):
        if not isinstance(reference,str) or not re.fullmatch(r'm[1-9][0-9]{0,9}',reference):
            raise FlintError('invalid memory reference')
        row=self.db.execute('SELECT hash,content FROM memory_versions WHERE id=?',(int(reference[1:]),)).fetchone()
        if row is None:raise FlintError('memory not found')
        data=bytes(row[1])
        if len(data)>MAX_MEMORY or digest(data)!=row[0]:raise FlintError('memory integrity failure')
        return data

    def history(self,key,*,full=False):
        rows=self.db.execute('SELECT id,key,source,hash,previous,current FROM memory_versions WHERE key=? ORDER BY id',(key,)).fetchall()
        result=[]
        for r in rows:
            data=self.recover('m'+str(r[0]))
            item={'id':'m'+str(r[0]),'key':r[1],'source':r[2],'sha256':r[3],
                  'previous':'m'+str(r[4]) if r[4] else None,'current':bool(r[5])}
            if full:item['content']=data.decode()
            result.append(item)
        return result

    def forget(self,key):
        with self.db:
            row=self.db.execute('DELETE FROM memory_versions WHERE key=?',(key,))
        return row.rowcount>0

    def recall(self,query,*,limit=5,budget=8192):
        if not isinstance(query,str) or not query.strip() or len(query)>512:
            raise FlintError('query must contain 1..512 characters')
        if type(limit) is not int or not 1<=limit<=32 or type(budget) is not int or not 128<=budget<=131072:
            raise FlintError('recall requires limit 1..32 and budget 128..131072 bytes')
        wanted=set(terms(query))
        rows=self.db.execute('SELECT id,key,source,hash,tracked FROM memory_versions WHERE current=1 ORDER BY id').fetchall()
        if len(rows)>MAX_CURRENT:raise FlintError('memory index exceeds limit')
        active_bytes=self.db.execute('SELECT coalesce(sum(length(content)),0) FROM memory_versions WHERE current=1').fetchone()[0]
        if active_bytes>MAX_INDEX_BYTES:raise FlintError('memory index exceeds 512 KiB')
        docs=[]; stale=0
        for identifier,key,path,handle,tracked in rows:
            ref='m'+str(identifier); data=self.recover(ref)
            if tracked:
                try:live=self.store.file_bytes(path)
                except FlintError:stale+=1;continue
                if digest(live)!=handle:stale+=1;continue
            counts=Counter(terms(key+'\n'+data.decode()))
            docs.append({'id':ref,'key':key,'content':data.decode(),'terms':counts,'length':sum(counts.values())})
        n=len(docs); average=sum(d['length'] for d in docs)/n if n else 1
        frequency={term:sum(term in d['terms'] for d in docs) for term in wanted}
        candidates=[]
        for doc in docs:
            score=0.0
            for term in wanted:
                tf=doc['terms'][term]
                if tf:
                    idf=math.log(1+(n-frequency[term]+.5)/(frequency[term]+.5))
                    score+=idf*tf*2.2/(tf+1.2*(.25+.75*doc['length']/max(average,1)))
            if score>=.1:
                candidates.append((score,doc))
        candidates.sort(key=lambda pair:(-pair[0],int(pair[1]['id'][1:])))
        result={'hits':[],'omitted':len(candidates),'stale':stale}
        for score,doc in candidates:
            if len(result['hits'])>=limit:break
            hit={k:doc[k] for k in ('id','key','content')}
            proposal={'hits':result['hits']+[hit],'omitted':result['omitted']-1,'stale':stale}
            if len(render_recall(proposal).encode())<=budget:result=proposal
        return result
