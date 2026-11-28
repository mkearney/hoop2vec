"""scripts/site_data.sh: published data goes to a single-commit site-data branch, one league at a time."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "site_data.sh"
pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def clone(tmp, remote, name):
    d = tmp / name
    git(tmp, "clone", "-q", "-b", "main", str(remote), name)
    (d / "scripts").mkdir(exist_ok=True)
    shutil.copy(SCRIPT, d / "scripts" / "site_data.sh")
    return d


def run(cwd, *args):
    return subprocess.run(["bash", "scripts/site_data.sh", *args], cwd=cwd, env=ENV, check=True,
                          capture_output=True, text=True).stdout


def write(d, path, text):
    p = d / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_push_and_pull(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "-b", "main", "seed")
    write(seed, ".gitignore", "docs/*/data/\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "init")
    git(seed, "push", "-q", str(remote), "main")

    a, b = clone(tmp_path, remote, "a"), clone(tmp_path, remote, "b")
    run(a, "pull")                                                # no branch yet: not an error

    write(a, "docs/mens/data/ratings.json", "1")
    write(a, "docs/nba/data/ratings.json", "2")
    run(a, "push", "first", "mens", "nba")

    # another checkout publishes only its league, from data that is stale for the others
    run(b, "pull")
    write(b, "docs/nba/data/ratings.json", "3")
    write(b, "docs/mens/data/ratings.json", "stale")
    run(b, "push", "nba only", "nba")

    # a's next push replaces mens and keeps b's nba; files removed locally are removed on the branch
    write(a, "docs/mens/data/other.json", "4")
    (a / "docs/mens/data/ratings.json").unlink()
    run(a, "push", "mens only", "mens")
    assert "no changes" in run(a, "push", "again", "mens")

    files = git(a, "ls-tree", "-r", "--name-only", "origin/site-data").splitlines()
    assert files == ["docs/mens/data/other.json", "docs/nba/data/ratings.json"]
    assert git(a, "show", "origin/site-data:docs/nba/data/ratings.json") == "3"
    assert git(a, "rev-list", "--count", "origin/site-data") == "1"      # never more than one commit
    assert git(a, "status", "--porcelain", "--", "docs") == ""            # main's checkout stays clean

    c = clone(tmp_path, remote, "c")
    run(c, "pull")
    assert (c / "docs/mens/data/other.json").read_text() == "4" and (c / "docs/nba/data/ratings.json").read_text() == "3"


def test_rejects_bad_league(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main", "r")
    r = tmp_path / "r"
    (r / "scripts").mkdir()
    shutil.copy(SCRIPT, r / "scripts" / "site_data.sh")
    p = subprocess.run(["bash", "scripts/site_data.sh", "push", "msg", "../x"], cwd=r, env=ENV, capture_output=True, text=True)
    assert p.returncode == 2 and "bad league" in p.stderr
