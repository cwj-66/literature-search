import json
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.assistant_chats import Chat, ensure, read_all, remove, write
from app.infrastructure import demo, demo_recordings
from app.services.papers import workspaces


class RecordedDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(self.root / "papers.sqlite3"),
                                          "PAPER_DEMO_RECORDING_UPGRADE_VISITOR": "a" * 32})
        self.env.start()
        self.wid = "pmvcf313hr4xt"
        workspaces.register(self.wid, "RAG研究")
        seed = self.root / "demo-seed"
        seed.mkdir()
        shutil.copyfile(self.root / "papers.sqlite3", seed / "papers.sqlite3")
        with closing(sqlite3.connect(seed / "papers.sqlite3")) as db, db:
            ensure(db)
            for i, count in enumerate((1, 3, 5), 1):
                chat = {"id": f"demo-reviewed-{i}", "title": f"Recorded {i}", "selectedIds": [],
                        "turns": [{"task": {"task_id": f"recorded-{i}-{j}"}} for j in range(count)],
                        "recorded_demo": {"kind": "recorded_live_model_run"}}
                db.execute("INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)",
                           (self.wid, chat["id"], json.dumps(chat), 1, "stamp"))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def old_visitor(self, ident):
        folder = demo.root() / ident
        folder.mkdir(parents=True)
        shutil.copyfile(self.root / "papers.sqlite3", folder / "papers.sqlite3")
        (folder / "generation").write_text("existing-generation")
        return folder

    def test_existing_visitor_receives_samples_without_replacing_private_chat(self):
        ident = "a" * 32
        self.old_visitor(ident)
        with demo.scope(ident):
            original = write(self.wid, Chat(id="private", title="你好", turns=[{"question": "我的问题"}]))
        demo.initialize(ident)
        demo.initialize(ident)
        with demo.scope(ident):
            chats = read_all(self.wid)["chats"]
        self.assertEqual(len(chats), 4)
        self.assertEqual(next(c for c in chats if c["id"] == "private"), original)
        self.assertEqual(sorted(len(c["turns"]) for c in chats if c["id"] != "private"), [1, 3, 5])
        self.assertEqual((demo.root() / ident / "generation").read_text(), "existing-generation")

    def test_deleting_new_sample_persists_and_does_not_affect_other_visitor(self):
        a, b = "a" * 32, "b" * 32
        self.old_visitor(a)
        demo.initialize(a)
        with demo.scope(a):
            chat = read_all(self.wid)["chats"][0]
            remove(self.wid, chat["id"], chat["version"])
        demo.initialize(a)
        demo.initialize(b)
        with demo.scope(a):
            self.assertEqual(len(read_all(self.wid)["chats"]), 2)
        with demo.scope(b):
            self.assertEqual(len(read_all(self.wid)["chats"]), 3)

    def test_new_seed_and_author_runs_are_not_duplicated(self):
        ident = "a" * 32
        demo.initialize(ident)
        with demo.scope(ident):
            self.assertEqual(len(read_all(self.wid)["chats"]), 3)
            self.assertTrue(all(c["id"].startswith("demo-reviewed-") for c in read_all(self.wid)["chats"]))
        author = "b" * 32
        self.old_visitor(author)
        with demo.scope(author):
            for i, count in enumerate((1, 3, 5), 1):
                write(self.wid, Chat(id=f"author-{i}", title="Renamed", turns=[
                    {"task": {"task_id": f"recorded-{i}-{j}"}} for j in range(count)]))
        with patch.dict(os.environ, {"PAPER_DEMO_RECORDING_UPGRADE_VISITOR": author}):
            demo.initialize(author)
        with demo.scope(author):
            self.assertEqual(len(read_all(self.wid)["chats"]), 3)

    def test_unselected_existing_visitor_is_not_migrated(self):
        ident = "b" * 32
        self.old_visitor(ident)
        demo.initialize(ident)
        with demo.scope(ident):
            self.assertEqual(read_all(self.wid)["chats"], [])
