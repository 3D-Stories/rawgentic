"""Tests for hooks/atomic_write_lib.py — the ONE shared atomic write (#264, C6)."""
import os
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from atomic_write_lib import atomic_write_text  # noqa: E402


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.json"
        atomic_write_text(target, '{"a": 1}\n')
        assert target.read_text() == '{"a": 1}\n'

    def test_replaces_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old")
        atomic_write_text(target, "new")
        assert target.read_text() == "new"

    def test_mkdir_creates_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "out.txt"
        atomic_write_text(target, "x", mkdir=True)
        assert target.read_text() == "x"

    def test_no_mkdir_raises_when_parent_missing(self, tmp_path):
        target = tmp_path / "missing" / "out.txt"
        with pytest.raises(OSError):
            atomic_write_text(target, "x")

    def test_no_stray_tmp_on_success(self, tmp_path):
        atomic_write_text(tmp_path / "out.txt", "x")
        assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]

    @pytest.mark.parametrize("exc_type", [Exception, KeyboardInterrupt, SystemExit])
    def test_no_stray_tmp_on_write_failure(self, tmp_path, monkeypatch, exc_type):
        """The temp must be unlinked when the write itself blows up — including
        BaseException shapes (KeyboardInterrupt/SystemExit): the except clause
        must stay `BaseException`, not `Exception`."""
        real_fdopen = os.fdopen

        def exploding_fdopen(fd, *a, **k):
            f = real_fdopen(fd, *a, **k)
            f.write = lambda *_: (_ for _ in ()).throw(exc_type("disk"))
            return f

        monkeypatch.setattr(os, "fdopen", exploding_fdopen)
        with pytest.raises(exc_type):
            atomic_write_text(tmp_path / "out.txt", "x")
        assert list(tmp_path.iterdir()) == [], "stray temp survived the failure"

    def test_fsync_flag_flushes_before_replace(self, tmp_path, monkeypatch):
        """fsync=True must fsync the temp fd before os.replace (suspend-state
        durability contract)."""
        calls = []
        real_fsync, real_replace = os.fsync, os.replace
        monkeypatch.setattr(os, "fsync", lambda fd: calls.append("fsync") or real_fsync(fd))
        monkeypatch.setattr(os, "replace", lambda *a: calls.append("replace") or real_replace(*a))
        atomic_write_text(tmp_path / "o.json", "{}", fsync=True)
        assert calls == ["fsync", "replace"]
        atomic_write_text(tmp_path / "o2.json", "{}")
        assert calls.count("fsync") == 1, "fsync must be opt-in"

    def test_custom_prefix_used_for_tmp(self, tmp_path, monkeypatch):
        """A site-specific prefix reaches mkstemp (observability contract)."""
        seen = {}
        import tempfile as _tf
        real = _tf.mkstemp

        def spy(**kw):
            seen.update(kw)
            return real(**kw)

        monkeypatch.setattr("atomic_write_lib.tempfile.mkstemp", spy)
        atomic_write_text(tmp_path / "o.txt", "x", prefix=".mysite.")
        assert seen["prefix"] == ".mysite."

    def test_old_content_survives_failed_write(self, tmp_path, monkeypatch):
        """Crash mid-write must leave the OLD file intact (the whole point)."""
        target = tmp_path / "out.txt"
        target.write_text("precious")
        monkeypatch.setattr(os, "replace",
                            lambda *a: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(OSError):
            atomic_write_text(target, "new")
        assert target.read_text() == "precious"


class TestAllSitesRouted:
    """#264 structural pin: no inline mkstemp/tmp+replace outside the helper.
    All pre-#264 python sites must import atomic_write_lib instead. Known
    deliberate exclusion: session-start's embedded `python3 -c` registry-write
    snippet (can't import from an inline string; consolidation tracked in
    review child 4d)."""

    SITES = ["notes-size-handler.py", "registry_prune.py",
             "post_update_reconcile.py", "scanner_bootstrap.py",
             "plan_lib.py", "adversarial_review_lib.py",
             "headless_interaction.py", "external_ref_lib.py"]

    @pytest.mark.parametrize("site", SITES)
    def test_site_imports_helper(self, site):
        text = (HOOKS_DIR / site).read_text()
        assert "from atomic_write_lib import" in text, (
            f"{site} must route atomic writes through atomic_write_lib")

    @pytest.mark.parametrize("site", SITES)
    def test_no_inline_mkstemp(self, site):
        text = (HOOKS_DIR / site).read_text()
        assert "mkstemp" not in text, (
            f"{site} carries an inline mkstemp — route through atomic_write_lib")

    @pytest.mark.parametrize("site", ["headless_interaction.py", "external_ref_lib.py"])
    def test_no_fixed_name_tmp_variants(self, site):
        """The two weaker fixed-name variants the Step-11 sweep found (no
        unlink-on-exception) must be gone."""
        text = (HOOKS_DIR / site).read_text()
        assert 'path + ".tmp"' not in text
        assert 'with_suffix(".json.tmp")' not in text

    def test_plan_lib_no_fixed_name_tmp(self):
        """plan_lib's weaker variant (fixed '.tmp' name, no unlink-on-exception)
        must be gone."""
        text = (HOOKS_DIR / "plan_lib.py").read_text()
        assert 'tmp = path + ".tmp"' not in text

    def test_adversarial_review_no_inline_nofollow_block(self):
        text = (HOOKS_DIR / "adversarial_review_lib.py").read_text()
        assert "O_NOFOLLOW" not in text, (
            "adversarial_review_lib's bespoke tmp+replace must route through "
            "the helper (mkstemp's O_EXCL covers the symlink defense)")


class TestPlanLibGainsUnlink:
    """#264 AC: plan_lib gains unlink-on-exception — a failed review-state
    write must not leave a stray temp behind."""

    def test_write_review_state_failure_leaves_no_tmp(self, tmp_path, monkeypatch):
        import plan_lib
        # Fail the final rename in BOTH the module under test and the helper.
        boom = lambda *a: (_ for _ in ()).throw(OSError("boom"))  # noqa: E731
        monkeypatch.setattr(plan_lib.os, "replace", boom, raising=True)
        import atomic_write_lib as _awl
        monkeypatch.setattr(_awl.os, "replace", boom, raising=True)
        with pytest.raises(OSError):
            plan_lib.write_review_state(str(tmp_path), "feat/x", "applied")
        state_dir = Path(plan_lib.review_state_path(str(tmp_path), "feat/x")).parent
        strays = [p for p in state_dir.iterdir()
                  if ".tmp" in p.name or p.name.startswith(".atomic")]
        assert strays == [], f"stray temp(s) survived: {strays}"
