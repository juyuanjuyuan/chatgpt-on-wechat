import unittest

from mcp_api_server.app.profile_extraction_utils import (
    candidate_status_label,
    clean_profile_value,
    is_profile_extraction_candidate_status,
    normalize_candidate_status,
)

class CandidateProfileExtractionTest(unittest.TestCase):
    def test_status_aliases_are_normalized(self):
        self.assertEqual(normalize_candidate_status("未发送照片"), "pending_photo")
        self.assertEqual(normalize_candidate_status("已发送照片"), "pending_review")
        self.assertEqual(normalize_candidate_status("审核中"), "reviewing")
        self.assertEqual(normalize_candidate_status("need_more_photo"), "need_more_photo")

    def test_only_pending_photo_is_idle_extraction_target(self):
        self.assertTrue(is_profile_extraction_candidate_status("pending_photo"))
        self.assertFalse(is_profile_extraction_candidate_status("pending_review"))
        self.assertFalse(is_profile_extraction_candidate_status("passed"))

    def test_profile_empty_values_are_dropped(self):
        self.assertIsNone(clean_profile_value("未知", 64))
        self.assertIsNone(clean_profile_value(" ", 64))
        self.assertEqual(clean_profile_value("上海", 64), "上海")

    def test_status_label_mapping(self):
        self.assertEqual(candidate_status_label("pending_photo"), "未发送照片")
        self.assertEqual(candidate_status_label("pending_review"), "已发送照片")


if __name__ == "__main__":
    unittest.main()
