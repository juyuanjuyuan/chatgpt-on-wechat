import unittest

from common.cowagent_runtime import is_underage, is_photo_refusal, increase_refusal_and_check_stop, _REFUSAL_COUNTER


class CowAgentRuntimePolicyTest(unittest.TestCase):
    def setUp(self):
        _REFUSAL_COUNTER.clear()

    def test_underage_detection(self):
        self.assertTrue(is_underage("我17岁可以吗"))
        self.assertTrue(is_underage("我是未成年"))
        self.assertFalse(is_underage("我19岁，做过主播"))

    def test_refusal_detection(self):
        self.assertTrue(is_photo_refusal("不方便发照片"))
        self.assertTrue(is_photo_refusal("我拒绝"))
        self.assertFalse(is_photo_refusal("我可以发图"))

    def test_refusal_counter_stop_after_two(self):
        key = "session-1"
        self.assertFalse(increase_refusal_and_check_stop(key))
        self.assertFalse(increase_refusal_and_check_stop(key))
        self.assertTrue(increase_refusal_and_check_stop(key))


if __name__ == '__main__':
    unittest.main()
