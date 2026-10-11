"""Offline tests: content safety, partitioning, result ledger."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from scripts.mirror_all_skills_hub import archive_bundle, clean_path, mirror_entries, shard_number

class FakeSource:
    def source_id(self):
        return "official"
    def fetch(self, identifier):
        if identifier.endswith("bad"):
            return None
        return SimpleNamespace(files={"SKILL.md": "---\nname: test\n---\n# Test", "references/note.md": "hello"})

class MirrorTests(unittest.TestCase):
    def test_partition_deterministic(self):
        for token in ("official/a", "github/b", "clawhub/c"):
            idx = shard_number(token, 5)
            self.assertEqual(idx, shard_number(token, 5))
            self.assertTrue(0 <= idx < 5)
    def test_unsafe_paths(self):
        for name in ("../evil", "/root", "x/../y", "a\\b"):
            with self.assertRaises(ValueError):
                clean_path(name)
    def test_zip(self):
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as z:
            result = archive_bundle(z, "quarantine/a", SimpleNamespace(files={"SKILL.md": "ok"}))
        self.assertEqual(result["files"], 1)
        with zipfile.ZipFile(io.BytesIO(target.getvalue())) as z:
            self.assertEqual(z.read("quarantine/a/SKILL.md"), b"ok")
    def test_full_shard_and_results(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeSource()
            entries = [{"identifier": "official/ok", "source": "official"},
                       {"identifier": "official/bad", "source": "official"}]
            result = mirror_entries(entries, shard=0, shards=1, out=Path(folder),
                                    sources={"official": fake}, index_source=fake, delay=0)
            self.assertEqual(result["attempted"], 2)
            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(result["unavailable"], 1)
            with zipfile.ZipFile(Path(folder) / "skill-bundles-000.zip") as z:
                self.assertEqual(len(z.namelist()), 2)

if __name__ == "__main__":
    unittest.main()
