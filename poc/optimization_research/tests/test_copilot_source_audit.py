import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from verify_copilot_source_audit import aggregate_all_occurrences


class SourceAuditTests(unittest.TestCase):
    def test_reused_group_ids_do_not_drop_calls_and_unknown_is_not_zero(self):
        calls=[{'message_id':'same','timestamp':'2026-06-05T00:04:59.9999999Z','result':'Success','tokens':{'prompt':10}},
               {'message_id':'same','timestamp':'2026-06-05T00:05:00Z','result':'Success','tokens':{'prompt':None}},
               {'message_id':'third','timestamp':'2026-06-05T00:05:01Z','result':'Success','tokens':{'prompt':0}},
               {'message_id':'outside','timestamp':'2026-06-08T00:00:00Z','result':'Success','tokens':{'prompt':123}}]
        row={'session_id':'s','date':'2026-06-05','turns':[{'turn_id':'t','llm_calls':calls}]}
        payload=gzip.compress((json.dumps(row)+'\n').encode())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'date.2026-06-05.tar.gz'
            with tarfile.open(path,'w:gz') as out:
                entry=tarfile.TarInfo('date=2026-06-05/shard.jsonl.gz');entry.size=len(payload)
                out.addfile(entry,io.BytesIO(payload))
            tokens,known,total,a=aggregate_all_occurrences([path])
        self.assertEqual(total[:2].tolist(),[1,2]);self.assertEqual(known[:2].tolist(),[1,1])
        self.assertEqual(tokens[:2].tolist(),[10,0]);self.assertEqual(a['llm_calls'],4)
        self.assertEqual(a['out_of_window_calls'],1);self.assertEqual(a['repeated_grouping_ids'],1)
        self.assertEqual(a['exact_repeated_call_records'],0)
        self.assertEqual(a['invalid_prompt_types'],{'NoneType':1})
        self.assertEqual(a['invalid_prompt_status'],{'Success':1})


if __name__=='__main__':unittest.main()
