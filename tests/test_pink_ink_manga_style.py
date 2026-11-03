"""Run standalone: python -m unittest tests/test_pink_ink_manga_style.py"""
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "plugins" / "pink-ink-manga-style" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("pink_ink_manga_style", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeContext:
    def __init__(self):
        self.tools = {}
        self.commands = {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = (toolset, schema, handler)

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler


class PluginTests(unittest.TestCase):
    def test_valid_preset(self):
        for name in ("paper_coral", "peach_line", "dark_rose"):
            data = MODULE.build_style_prompt("adult person with a coral hoodie", name)
            self.assertEqual(data["preset"], name)
            self.assertEqual(len(data["palette"]), 5)
            self.assertFalse(data["rendered_image"])

    def test_invalid_input(self):
        for sub, preset in (("", "paper_coral"), ("subject", "missing")):
            with self.assertRaises(ValueError):
                MODULE.build_style_prompt(sub, preset)

    def test_register_and_call(self):
        ctx = FakeContext()
        MODULE.register(ctx)
        self.assertIn("pink_ink_style_prompt", ctx.tools)
        self.assertIn("pink-ink", ctx.commands)
        handler = ctx.tools["pink_ink_style_prompt"][2]
        payload = json.loads(handler({"subject": "adult character"}))
        self.assertIn("hand-inked", payload["positive_prompt"])
        self.assertIn("photorealism", payload["negative_prompt"])
        self.assertIn("positive_prompt", json.loads(ctx.commands["pink-ink"]("adult portrait")))


if __name__ == "__main__":
    unittest.main()
