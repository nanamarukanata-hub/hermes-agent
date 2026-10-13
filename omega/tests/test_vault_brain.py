import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import vault_brain as b

class VaultBrainTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.vault=Path(self.temp.name)/'Vault'; self.vault.mkdir()
        self.db=Path(self.temp.name)/'db.sqlite'
        (self.vault/'A.md').write_text('---\nstatus: active\nsource_url: https://example.org/a\n---\n# A\n音楽理論の研究 [[B]] [[Missing]]',encoding='utf-8')
        (self.vault/'B.md').write_text('# B\nA digital synthesizer',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def output(self,fn,*args):
        f=io.StringIO()
        with contextlib.redirect_stdout(f):fn(*args)
        return json.loads(f.getvalue())
    def test_index_and_recall(self):
        result=self.output(b.scan,self.vault,self.db)
        self.assertEqual(result['indexed'],2)
        hits=self.output(b.search,self.db,'digital',10)
        self.assertEqual(hits[0]['path'],'B.md')
        cjk=self.output(b.search,self.db,'音楽理論',10)
        self.assertEqual(cjk[0]['path'],'A.md')
        self.assertEqual(cjk[0]['source'],'https://example.org/a')
    def test_graph_audit(self):
        self.output(b.scan,self.vault,self.db)
        report=self.output(b.audit,self.db)
        self.assertEqual(len(report['unresolved']),1)
        self.assertEqual(report['unresolved'][0]['unresolved'],'Missing')
    def test_lifecycle(self):
        created=self.output(b.remember,self.vault,'test/project','Temporary fact','explicit user statement')
        fact=created['created'];self.assertTrue((self.vault/fact).exists())
        self.output(b.scan,self.vault,self.db)
        self.assertEqual(len(self.output(b.search,self.db,'Temporary fact',10)),1)
        self.output(b.transition,self.vault,fact,'superseded','Corrected fact','explicit correction')
        self.output(b.scan,self.vault,self.db)
        self.assertEqual(len(self.output(b.search,self.db,'Temporary fact',10)),0)
        self.assertEqual(len(self.output(b.search,self.db,'Corrected fact',10)),1)
        active=[p for p in (self.vault/'03_Knowledge'/'Memory').glob('*.md') if 'status: active' in p.read_text()]
        self.assertEqual(len(active),1)
        self.output(b.transition,self.vault,str(active[0].relative_to(self.vault)),'withdrawn')
        self.output(b.scan,self.vault,self.db)
        self.assertEqual(len(self.output(b.search,self.db,'Corrected fact',10)),0)
    def test_ignore_excluded_and_idempotent(self):
        (self.vault/'.obsidian').mkdir();(self.vault/'.obsidian'/'secret.md').write_text('secret data')
        first=self.output(b.scan,self.vault,self.db)
        second=self.output(b.scan,self.vault,self.db)
        self.assertEqual(first['indexed'],2)
        self.assertEqual(second['indexed'],2)
        (self.vault/'B.md').unlink()
        final=self.output(b.scan,self.vault,self.db)
        self.assertEqual(final['removed'],1)

if __name__=='__main__':unittest.main()
