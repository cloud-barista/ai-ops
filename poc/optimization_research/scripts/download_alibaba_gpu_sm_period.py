"""Download all pod columns/rows for fixed days123-129, with resumable day journals."""
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time

BASE_SCRIPT = Path('/home/researcher/ai-mcmp/research/scripts/download_alibaba_gpu_sm_future.py')
BASE_SHA = 'b3ccf91de566da16580f193a2d6407318d5a4b5c6963d9416d633370bca09732'
ROOT = Path('/tmp/ai_mcmp_sm_hour_v3/data/alibaba_gpu_2026_days123_129')
DAYS = range(123, 130)


def load_base():
    if hashlib.sha256(BASE_SCRIPT.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError('Pinned range downloader changed')
    spec = importlib.util.spec_from_file_location('alibaba_range_base', BASE_SCRIPT)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.ROOT, base.DAYS, base.SPOOL = ROOT, DAYS, ROOT / '.range_spool'
    return base


def valid_day(path, entries):
    if not path.exists():
        return None
    journal = json.loads(path.read_text())
    if [e['name'] for e in journal['entries']] != [e['name'] for e in entries]:
        raise ValueError('Resume partition set changed')
    for entry in journal['entries']:
        p = ROOT / entry['name']
        if not p.exists() or p.stat().st_size != entry['uncompressed_bytes']:
            return None
        h = hashlib.sha256()
        with p.open('rb') as f:
            for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != entry['sha256']:
            return None
    return journal


def main():
    if (ROOT / 'MANIFEST.json').exists():
        raise ValueError('Complete data already exists; refusing another download')
    base = load_base()
    ROOT.mkdir(parents=True, exist_ok=True)
    base.SPOOL.mkdir(exist_ok=True)
    journal_dir = ROOT / '.download_journal'
    journal_dir.mkdir(exist_ok=True)
    table = 'asi_opensource_pod_hourly'
    url, size, etag, by_day = base.central_entries(table)
    for entries in by_day.values():
        for entry in entries:
            if '..' in Path(entry['name']).parts or not (ROOT / entry['name']).resolve().is_relative_to(ROOT.resolve()):
                raise ValueError('Unsafe archive path')
    total = sum(e['uncompressed_bytes'] for es in by_day.values() for e in es)
    if total > 25 * 1024**3 or shutil.disk_usage(ROOT).free < total * 2 + 20 * 1024**3:
        raise ValueError('Fixed seven-day disk budget exceeded')
    print(json.dumps({'precheck': True, 'days': list(DAYS), 'selected_bytes': total,
                      'archive_bytes': size, 'etag': etag}), flush=True)
    done, pending = [], []
    for day, entries in by_day.items():
        old = valid_day(journal_dir / f'day{day}.json', entries)
        if old:
            done.append(old)
            print(json.dumps({'resumed_day': day}), flush=True)
        else:
            pending.append((day, entries))
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(base.extract_day, table, day, (url, entries)): day for day, entries in pending}
        for task in concurrent.futures.as_completed(tasks):
            day = tasks[task]
            record = task.result()
            path = journal_dir / f'day{day}.json'
            part = path.with_suffix('.json.part')
            part.write_text(json.dumps(record, indent=2) + '\n')
            part.replace(path)
            done.append(record)
            print(json.dumps({'completed_days': len(done), 'day': day,
                              'bytes': sum(e['uncompressed_bytes'] for e in record['entries']),
                              'rows': sum(e['rows'] for e in record['entries']),
                              'seconds': time.monotonic() - started}), flush=True)
    files = sorted([e for d in done for e in d['entries']], key=lambda e: (e['day'], e['hour']))
    summary = {table: {'files': len(files), 'bytes': total, 'rows': sum(e['rows'] for e in files),
                      'column_count': files[0]['columns'], 'columns': files[0]['schema']}}
    manifest = {'dataset': 'Alibaba Cluster Trace GPU v2026 selected temporal partitions',
                'period': 'relative day=123..129 inclusive, all 24 hours',
                'tables': {table: {'url': url, 'archive_bytes': size, 'etag': etag,
                                  'members': len(files), 'selected_bytes': total}},
                'summary': summary, 'files': files,
                'downloaded_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'downloader_base_sha256': BASE_SHA,
                'method': 'HTTP ZIP member ranges; CRC32 and SHA256 per complete original Parquet; no row filtering'}
    (ROOT / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (ROOT / 'SOURCE.md').write_text('''# Alibaba GPU v2026: days123-129\n\nAll 168 pod_hourly partitions, all original rows and columns are retained.\nSelected before reading measurement results: days123-124 extend training, day125 selects models, days126-129 confirm fixed models.\nRelative trace day indices are not calendar dates.\nOfficial source: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2026\nOfficial schema: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/schema.md\nOfficial archives: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-gpu-v2026/docs/data_download.md\nStored Parquet ZIP members were fetched with HTTP Range; CRC32 verified, SHA256 and row counts recorded in MANIFEST.json. The full351.8GB archive was not saved.\nThe trace does not contain our platform's scale actions or user SLO outcomes.\n''')
    # Only temporary range spool is removed; all original Parquet partitions stay.
    base.SPOOL.rmdir()
    print(json.dumps({'complete': True, 'summary': summary, 'seconds': time.monotonic() - started}), flush=True)


if __name__ == '__main__':
    main()
