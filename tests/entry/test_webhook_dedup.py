from zd_agent.services import atomic_write
from zd_agent.services.webhook_dedup import WebhookDedup


def test_seen_returns_false_for_new_key(tmp_path):
    d = WebhookDedup(tmp_path / "d.json")
    assert d.seen("x") is False


def test_add_then_seen(tmp_path):
    d = WebhookDedup(tmp_path / "d.json")
    d.add("x")
    assert d.seen("x") is True


def test_persists_across_instances(tmp_path):
    p = tmp_path / "d.json"
    d1 = WebhookDedup(p)
    d1.add("x")
    d2 = WebhookDedup(p)
    assert d2.seen("x") is True


def test_max_size_lru_eviction(tmp_path):
    d = WebhookDedup(tmp_path / "d.json", max_size=3)
    d.add("a")
    d.add("b")
    d.add("c")
    d.add("d")
    assert d.seen("a") is False  # evicted
    assert d.seen("d") is True


def test_add_same_key_twice_refreshes_lru(tmp_path):
    d = WebhookDedup(tmp_path / "d.json", max_size=2)
    d.add("a")
    d.add("b")
    d.add("a")  # refreshes 'a'
    d.add("c")
    # 'a' refreshed so 'b' should be evicted, 'a' retained
    assert d.seen("b") is False
    assert d.seen("a") is True
    assert d.seen("c") is True


def test_corrupted_file_yields_empty(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("not json", encoding="utf-8")
    d = WebhookDedup(p)
    assert len(d) == 0


def test_concurrent_save_interleaving_does_not_crash(tmp_path, monkeypatch):
    path = tmp_path / "d.json"
    first = WebhookDedup(path)
    second = WebhookDedup(path)

    original_replace = atomic_write.os.replace
    interleaved = False

    def replace_with_interleaved_save(src, dst):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            second.add("second")
        return original_replace(src, dst)

    monkeypatch.setattr(atomic_write.os, "replace", replace_with_interleaved_save)

    first.add("first")

    assert interleaved is True
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_len(tmp_path):
    d = WebhookDedup(tmp_path / "d.json")
    assert len(d) == 0
    d.add("x")
    assert len(d) == 1
