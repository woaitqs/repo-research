import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from minipstack.registry import resolve_lane  # noqa: E402


class Lab:
    def __init__(self, root):
        self.root = os.path.realpath(str(root))
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        fake = os.path.join(HERE, "fakecli.py")
        for name in ("claude", "codex", "grok", "devin"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write(f"#!{sys.executable} -I\nimport runpy, sys\nsys.argv = [{fake!r}, {self.bin!r}, {name!r}, *sys.argv[1:]]\n"
                        f"runpy.run_path({fake!r}, run_name='__main__')\n")
            os.chmod(path, 0o755)
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        git = lambda *a: subprocess.run(["git", "-C", self.repo, *a], check=True, capture_output=True)
        git("init", "-q", "--initial-branch=main")
        git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "init")
        self.worktree = os.path.join(self.root, "wt")
        git("worktree", "add", "-q", self.worktree, "-b", "lane")
        self.env = {"PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), "HOME": self.root, "LANG": "C"}
        self.n = 0

    def control(self, **value):
        for name in os.listdir(self.bin):
            if name.endswith(".count") or name == "calls.jsonl":
                os.unlink(os.path.join(self.bin, name))
        with open(os.path.join(self.bin, "control.json"), "w") as f:
            json.dump(value, f)

    def calls(self):
        path = os.path.join(self.bin, "calls.jsonl")
        return [json.loads(line) for line in open(path)] if os.path.exists(path) else []

    def lane(self, provider, *, parent="claude", model="opus", effort="max", mode="read-only", cwd=None, timeout_s=None,
             prompt="review this"):
        self.n += 1
        out = os.path.join(self.root, "out", str(self.n))
        os.makedirs(out)
        prompt_path = os.path.join(out, "prompt.md")
        with open(prompt_path, "w") as f:
            f.write(prompt)
        return resolve_lane(parent=parent, provider=provider, model=model, effort=effort, mode=mode, prompt_path=prompt_path,
                            cwd=cwd or self.repo, output_path=os.path.join(out, "answer.md"),
                            receipt_path=os.path.join(out, "receipt.json"), timeout_s=timeout_s)


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)
