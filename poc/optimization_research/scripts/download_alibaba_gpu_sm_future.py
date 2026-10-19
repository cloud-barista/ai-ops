from __future__ import annotations
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import tempfile
import time
import zlib
from pathlib import Path

import pyarrow.parquet as pq
import requests

BASE = 'https://tre-clusterdata.oss-cn-hangzhou.aliyuncs.com/cluster-trace-gpu-v2026/data/'
TABLES = ['asi_opensource_pod_hourly']
DAYS = range(116, 123)
ROOT = Path('/tmp/ai_mcmp_sm_confirm_v1/data/alibaba_gpu_2026_days116_122')
EXPECTED_ETAGS = {
 'asi_opensource_pod_hourly': '"092823B8D7C37992AF1BB732C8865E58-400"',
 'asi_opensource_server_hourly': '"EAD6B2EC97633BFF060F8019B8EA28C8-368"',
 'asi_opensource_network_hourly': '"3EF8FBE5FE6E0CDAFE6553A71A71BFF3-49"',
}
SPOOL = ROOT / '.range_spool'


def request_range(session, url, start, end, dest):
    r = session.get(url, headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'}, stream=True, timeout=(30, 180))
    r.raise_for_status()
    if r.status_code != 206:
        raise RuntimeError(f'expected HTTP 206, got {r.status_code} for {start}-{end}')
    expected = end - start + 1
    cr = r.headers.get('Content-Range', '')
    if not cr.startswith(f'bytes {start}-{end}/'):
        raise RuntimeError(f'wrong Content-Range: {cr}')
    n = 0
    with dest.open('wb') as f:
        for chunk in r.iter_content(4 * 1024 * 1024):
            if chunk:
                f.write(chunk); n += len(chunk)
    r.close()
    if n != expected:
        raise RuntimeError(f'range length mismatch {n} != {expected}')
    return n


def central_entries(table):
    url = BASE + table + '.zip'
    s = requests.Session()
    h = s.head(url, allow_redirects=True, timeout=30); h.raise_for_status()
    archive_size = int(h.headers['Content-Length'])
    etag = h.headers.get('ETag')
    if etag != EXPECTED_ETAGS[table]:
        raise RuntimeError(f'{table} source ETag changed: {etag}')
    tail_start = max(0, archive_size - 1024 * 1024)
    tail_path = Path(tempfile.mktemp(prefix='alibaba_zip_tail_'))
    request_range(s, url, tail_start, archive_size - 1, tail_path)
    tail = tail_path.read_bytes(); tail_path.unlink()
    eocd = tail.rfind(b'PK\x05\x06')
    if eocd < 0: raise RuntimeError(f'{table}: ZIP EOCD not found')
    vals = struct.unpack_from('<4s4H2IH', tail, eocd)
    _, _, _, _, n_total, cd_size, cd_off, _ = vals
    if n_total == 0xffff or cd_size == 0xffffffff or cd_off == 0xffffffff:
        loc = eocd - 20
        sig, _, zoff, _ = struct.unpack_from('<4sIQI', tail, loc)
        if sig != b'PK\x06\x07': raise RuntimeError('ZIP64 locator missing')
        zpath = Path(tempfile.mktemp(prefix='alibaba_zip64_'))
        request_range(s, url, zoff, zoff + 55, zpath)
        z = zpath.read_bytes(); zpath.unlink()
        sig, _, _, _, _, _, _, n_total, cd_size, cd_off = struct.unpack('<4sQHHIIQQQQ', z)
        if sig != b'PK\x06\x06': raise RuntimeError('ZIP64 EOCD missing')
    cdpath = Path(tempfile.mktemp(prefix='alibaba_cdir_'))
    request_range(s, url, cd_off, cd_off + cd_size - 1, cdpath)
    cdir = cdpath.read_bytes(); cdpath.unlink()
    entries=[]; pos=0
    while pos < len(cdir):
        v=struct.unpack_from('<4s6H3I5H2I', cdir, pos)
        if v[0] != b'PK\x01\x02': raise RuntimeError(f'{table}: bad central entry at {pos}')
        method, crc, comp, uncomp = v[4], v[7], v[8], v[9]
        fnlen, exlen, commentlen = v[10], v[11], v[12]
        local = v[16]
        name_bytes=cdir[pos+46:pos+46+fnlen]
        flags=v[3]
        name=name_bytes.decode('utf-8' if flags & 0x800 else 'cp437')
        extra=cdir[pos+46+fnlen:pos+46+fnlen+exlen]
        if comp == 0xffffffff or uncomp == 0xffffffff or local == 0xffffffff:
            q=0
            while q+4 <= len(extra):
                eid,n=struct.unpack_from('<HH',extra,q); data=extra[q+4:q+4+n]
                if eid == 1:
                    k=0
                    if uncomp == 0xffffffff: uncomp=struct.unpack_from('<Q',data,k)[0]; k+=8
                    if comp == 0xffffffff: comp=struct.unpack_from('<Q',data,k)[0]; k+=8
                    if local == 0xffffffff: local=struct.unpack_from('<Q',data,k)[0]
                    break
                q += 4+n
        if name.startswith(table + '/'):
            m=re.search(r'/day=(\d+)/hour=(\d+)/',name)
            if not m: raise RuntimeError(f'partition path not understood: {name}')
            day,hour=map(int,m.groups())
            if day in DAYS:
                entries.append({'name':name,'day':day,'hour':hour,'local_offset':local,'compressed_bytes':comp,'uncompressed_bytes':uncomp,'crc32':crc,'method':method})
        pos += 46+fnlen+exlen+commentlen
    if table == 'asi_opensource_network_hourly':
        # The official network archive contains only days 109–115.
        pass
    by_day={d:[] for d in DAYS}
    for e in entries: by_day[e['day']].append(e)
    for d, es in by_day.items():
        es.sort(key=lambda x:x['hour'])
        if len(es)!=24 or [e['hour'] for e in es] != list(range(24)):
            raise RuntimeError(f'{table} day={d}: expected 24 hourly files, got {len(es)}')
        for e in es:
            if e['method'] != 0: raise RuntimeError(f"{e['name']}: unexpected ZIP compression {e['method']}")
    return url, archive_size, etag, by_day


def extract_day_once(table, day, args):
    url, entries = args
    # ZIP entries are sorted by partition; one range per day includes only local headers,
    # tiny data descriptors, and the selected stored Parquet files.
    start=entries[0]['local_offset']
    last=entries[-1]
    last_header=fetch_local_header(url,last['local_offset'],last['name'])
    end=last['local_offset'] + 30 + last_header['name_len'] + last_header['extra_len'] + last['compressed_bytes'] - 1
    tmp=SPOOL/f'{table}_day{day:03d}.range.part'
    sess=requests.Session()
    n=request_range(sess,url,start,end,tmp)
    records=[]
    with tmp.open('rb') as src:
        for e in entries:
            src.seek(e['local_offset']-start)
            header=src.read(30)
            sig,ver,flags,method,mt,md,crc0,comp0,uncomp0,nlen,xlen=struct.unpack('<4s5H3I2H',header)
            name=src.read(nlen).decode('utf-8' if flags&0x800 else 'cp437')
            if sig != b'PK\x03\x04' or name!=e['name'] or method!=0: raise RuntimeError('local header mismatch')
            local={'name_len':nlen,'extra_len':xlen}
            data_start=e['local_offset']+30+local['name_len']+local['extra_len']
            data_end=data_start+e['compressed_bytes']
            if data_start < start or data_end > end+1:
                raise RuntimeError(f"{e['name']}: data outside selected range")
            rel=data_start-start
            src.seek(rel)
            out=ROOT/e['name']
            out.parent.mkdir(parents=True,exist_ok=True)
            part=out.with_suffix(out.suffix+'.part')
            crc=0; sha=hashlib.sha256(); remaining=e['compressed_bytes']
            with part.open('wb') as dst:
                while remaining:
                    b=src.read(min(4*1024*1024,remaining))
                    if not b: raise RuntimeError(f"truncated range while writing {e['name']}")
                    dst.write(b); crc=zlib.crc32(b,crc); sha.update(b); remaining-=len(b)
            crc &= 0xffffffff
            if crc != e['crc32']:
                part.unlink(missing_ok=True); raise RuntimeError(f"CRC mismatch: {e['name']}")
            if part.stat().st_size != e['uncompressed_bytes']:
                part.unlink(missing_ok=True); raise RuntimeError(f"size mismatch: {e['name']}")
            os.replace(part,out)
            pf=pq.ParquetFile(out)
            records.append({**e,'sha256':sha.hexdigest(),'rows':pf.metadata.num_rows,'columns':pf.metadata.num_columns,'schema':str(pf.schema_arrow)})
    tmp.unlink()
    return {'table':table,'day':day,'range_bytes':n,'entries':records}


def extract_day(table, day, args):
    for attempt in range(5):
        try:
            return extract_day_once(table, day, args)
        except (requests.RequestException, OSError) as exc:
            if attempt == 4: raise
            print(f'RETRY table={table} day={day} attempt={attempt+1} error={type(exc).__name__}',flush=True)
            time.sleep(min(2**attempt, 16))


def fetch_local_header(url, offset, expected_name):
    # Small independent range protects against assuming header lengths from central records.
    s=requests.Session()
    r=s.get(url,headers={'Range':f'bytes={offset}-{offset+4095}','Accept-Encoding':'identity'},timeout=(30,60))
    r.raise_for_status()
    if r.status_code != 206: raise RuntimeError('local header range did not return HTTP 206')
    b=r.content
    if len(b)<30: raise RuntimeError('short ZIP local header')
    sig,ver,flags,method,mt,md,crc,comp,uncomp,nlen,xlen=struct.unpack_from('<4s5H3I2H',b,0)
    if sig!=b'PK\x03\x04': raise RuntimeError(f'bad local signature at {offset}')
    raw=b[30:30+nlen]
    name=raw.decode('utf-8' if flags&0x800 else 'cp437')
    if name!=expected_name: raise RuntimeError(f'header name mismatch: {name} != {expected_name}')
    if method!=0: raise RuntimeError(f'local compression mismatch: {method}')
    return {'name_len':nlen,'extra_len':xlen}


def main():
    if ROOT.exists() and any(p.name != '.range_spool' for p in ROOT.iterdir()):
        raise RuntimeError(f'target is not empty; refusing to overwrite: {ROOT}')
    ROOT.mkdir(parents=True,exist_ok=True); SPOOL.mkdir(parents=True,exist_ok=True)
    table_info={}; day_tasks=[]
    for table in TABLES:
        url,size,etag,by_day=central_entries(table)
        table_info[table]={'url':url,'archive_bytes':size,'etag':etag,'members':sum(len(x) for x in by_day.values()),'selected_bytes':sum(e['compressed_bytes'] for es in by_day.values() for e in es)}
        for day,entries in by_day.items(): day_tasks.append((table,day,(url,entries)))
    total=sum(x['selected_bytes'] for x in table_info.values())
    if total > 25*1024**3 or shutil.disk_usage(ROOT).free < total*2 + 20*1024**3: raise RuntimeError('Download disk budget exceeded')
    print('PRECHECK',json.dumps({'selected_bytes':total,'tables':table_info},ensure_ascii=False),flush=True)
    done=[]; started=time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures={ex.submit(extract_day,t,d,a):(t,d) for t,d,a in day_tasks}
        for i,f in enumerate(concurrent.futures.as_completed(futures),1):
            t,d=futures[f]; rec=f.result(); done.append(rec)
            block=sum(e['compressed_bytes'] for e in rec['entries'])
            rows=sum(e['rows'] for e in rec['entries'])
            print(f'PROGRESS {i}/{len(futures)} table={t} day={d} bytes={block} rows={rows} elapsed_s={time.time()-started:.1f}',flush=True)
    files=[e for d in done for e in d['entries']]
    summary={}
    for t in TABLES:
        rows=[e for e in files if e['name'].startswith(t+'/')]
        summary[t]={'files':len(rows),'bytes':sum(e['compressed_bytes'] for e in rows),'rows':sum(e['rows'] for e in rows),'column_count':rows[0]['columns'],'columns':rows[0]['schema']}
    manifest={'dataset':'Alibaba Cluster Trace GPU v2026 selected temporal partitions','period':'relative day=116..122 inclusive, all 24 hours','tables':table_info,'summary':summary,'files':files,'downloaded_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'method':'HTTP byte-range fetch of selected ZIP local-file spans; ZIP CRC32 and SHA-256 verified per extracted Parquet; no full archive saved'}
    (ROOT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    (ROOT/'SOURCE.md').write_text('''# Alibaba Cluster Trace GPU v2026 — selected one-week slice\n\n- Official dataset: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2026\n- Official schema: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md\n- Official download sizes/layout: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md\n- Selected period: relative days 116–122 inclusive, all 24 hourly partitions. This is the next consecutive unused week after days109–115, fixed before reading measurements.\n- Tables included: pod-hourly only, with all original columns. Every hourly partition in this interval is retained. No rows were filtered.\n- Download method: HTTP byte ranges directly from stored Parquet members inside official ZIP objects. The 351.8 GB pod archive and the other full archives were not saved. Each extracted Parquet file was checked against the ZIP central-directory CRC32; SHA-256 and Parquet row counts are recorded in `MANIFEST.json`.\n- The `day` index is relative to the trace, not a calendar date. The schema does not include user request SLO or VM scale-action outcomes; this dataset supports workload/resource characterization and trace simulation, not direct causal evaluation of our autoscaling actions.\n- Download date: 2026-10-02 UTC. Cite the accompanying OSDI 2026 Alibaba trace paper as listed in the official README.\n''')
    shutil.rmtree(SPOOL)
    print('COMPLETE',json.dumps({'root':str(ROOT),'summary':summary,'selected_bytes':sum(v['bytes'] for v in summary.values()),'elapsed_s':time.time()-started},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
