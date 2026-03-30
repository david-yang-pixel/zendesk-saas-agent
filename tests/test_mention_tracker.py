import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zd_agent.mention.tracker import MentionTracker


class TestMentionTracker(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = Path(self.tmpdir) / ".state" / "processed_mentions.json"
        self.tracker = MentionTracker(self.state_file)

    def test_new_comment_is_not_processed(self) -> None:
        self.assertFalse(self.tracker.is_processed("comment-123"))

    def test_mark_processed_persists_across_instances(self) -> None:
        self.tracker.mark_processed("comment-123")
        tracker2 = MentionTracker(self.state_file)
        self.assertTrue(tracker2.is_processed("comment-123"))

    def test_is_processed_after_marking(self) -> None:
        self.tracker.mark_processed("comment-456")
        self.assertTrue(self.tracker.is_processed("comment-456"))

    def test_cleanup_removes_old_entries(self) -> None:
        old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        self.tracker._data["old-comment"] = old_ts
        self.tracker._save()
        self.tracker.cleanup(days=30)
        self.assertFalse(self.tracker.is_processed("old-comment"))

    def test_cleanup_keeps_recent_entries(self) -> None:
        self.tracker.mark_processed("recent-comment")
        self.tracker.cleanup(days=30)
        self.assertTrue(self.tracker.is_processed("recent-comment"))

    def test_comment_id_coerced_to_string(self) -> None:
        self.tracker.mark_processed(123)
        self.assertTrue(self.tracker.is_processed("123"))
        self.assertTrue(self.tracker.is_processed(123))

    def test_creates_parent_directory_if_missing(self) -> None:
        deep_path = Path(self.tmpdir) / "a" / "b" / "mentions.json"
        tracker = MentionTracker(deep_path)
        tracker.mark_processed("x")
        self.assertTrue(deep_path.exists())


if __name__ == "__main__":
    unittest.main()
