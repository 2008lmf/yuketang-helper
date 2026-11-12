"""离线单元测试：不联网、不登录，只验证纯逻辑。"""

from __future__ import annotations

import unittest
from datetime import datetime

from ykt.answer import QuestionBank, build_result, normalize, problems_from_presentation, submit_delay
from ykt.config import resolve_server
from ykt.qr import extract_url
from ykt.remind import describe, next_occurrence, remind_at
from ykt.wsclient import ws_url


class QRTest(unittest.TestCase):
    def test_extract_from_text(self):
        text = "扫码结果：https://www.yuketang.cn/api/v3/lesson/check-in/dynamic-qr-code?lessonId=123&enc=abc 请签到"
        self.assertEqual(
            extract_url(text),
            "https://www.yuketang.cn/api/v3/lesson/check-in/dynamic-qr-code?lessonId=123&enc=abc",
        )

    def test_ignore_other_url(self):
        self.assertIsNone(extract_url("https://example.com/hello"))
        self.assertIsNone(extract_url(""))


class AnswerTest(unittest.TestCase):
    single = {
        "problemId": "p1",
        "problemType": 1,
        "body": "下列哪一项是线性表的顺序存储结构",
        "options": [{"key": "A", "value": "数组"}, {"key": "B", "value": "链表"}],
        "answers": [],
    }
    multi = {
        "problemId": "p2",
        "problemType": 2,
        "body": "多选",
        "options": [{"key": "A", "value": "x"}, {"key": "B", "value": "y"}],
        "answers": [],
    }
    blank = {
        "problemId": "p3",
        "problemType": 4,
        "body": "填空",
        "options": None,
        "blanks": [{"num": 1, "answers": ["2"]}, {"num": 2, "answers": []}],
    }
    short = {"problemId": "p4", "problemType": 5, "body": "主观题", "answers": []}

    def test_normalize(self):
        self.assertEqual(normalize(" 下列哪一项？ "), "下列哪一项")

    def test_bank_hits_single_choice(self):
        bank = QuestionBank({"下列哪一项是线性表的顺序存储结构": ["A"]})
        result, source = build_result(self.single, {}, bank)
        self.assertEqual(result, ["A"])
        self.assertIn("题库", source)

    def test_option_text_maps_to_key(self):
        bank = QuestionBank({"下列哪一项是线性表的顺序存储结构": ["链表"]})
        result, _ = build_result(self.single, {}, bank)
        self.assertEqual(result, ["B"])

    def test_multiple_fallback_all(self):
        result, source = build_result(self.multi, {"fallback_multiple": "all"}, QuestionBank())
        self.assertEqual(result, ["A", "B"])
        self.assertIn("兜底", source)

    def test_single_fallback(self):
        result, _ = build_result(self.single, {"fallback_single": "B"}, QuestionBank())
        self.assertEqual(result, ["B"])

    def test_fill_blank_uses_slide_answers(self):
        result, _ = build_result(self.blank, {"fill_blank_empty": "无"}, QuestionBank())
        self.assertEqual(result, ["2", "无"])

    def test_short_answer_shape(self):
        result, _ = build_result(self.short, {"short_answer_text": "不会"}, QuestionBank())
        self.assertEqual(result, {"content": "不会", "pics": [], "videos": []})

    def test_submit_delay(self):
        self.assertEqual(submit_delay(0, {}), 0.0)
        self.assertAlmostEqual(submit_delay(100, {"delay_ratio": 0.5, "delay_max": 30}), 30.0)
        self.assertAlmostEqual(submit_delay(10, {"delay_ratio": 0.5, "delay_max": 0}), 5.0)

    def test_problems_from_presentation(self):
        pres = {"slides": [{"problem": self.single}, {"slide": 2}, {"problem": None}]}
        self.assertEqual(len(problems_from_presentation(pres)), 1)


class RemindTest(unittest.TestCase):
    entry = {"name": "高数", "weekday": 3, "time": "08:00", "room": "教三-201"}

    def test_next_occurrence_same_week(self):
        now = datetime(2026, 11, 9, 20, 0)  # 周一
        self.assertEqual(next_occurrence(self.entry, now), datetime(2026, 11, 11, 8, 0))

    def test_next_occurrence_rolls_to_next_week(self):
        now = datetime(2026, 11, 11, 9, 0)  # 周三 09:00，已过 08:00
        self.assertEqual(next_occurrence(self.entry, now), datetime(2026, 11, 18, 8, 0))

    def test_remind_at(self):
        now = datetime(2026, 11, 9, 20, 0)
        self.assertEqual(remind_at(self.entry, now, 10), datetime(2026, 11, 11, 7, 50))

    def test_describe(self):
        self.assertEqual(describe(self.entry), "周三 08:00 高数 @ 教三-201")


class MiscTest(unittest.TestCase):
    def test_resolve_server(self):
        self.assertEqual(resolve_server("changjiang"), "https://changjiang.yuketang.cn")
        self.assertEqual(resolve_server("https://pro.yuketang.cn/"), "https://pro.yuketang.cn")
        with self.assertRaises(ValueError):
            resolve_server("bogus")

    def test_ws_url(self):
        self.assertEqual(ws_url("https://www.yuketang.cn"), "wss://www.yuketang.cn/wsapp/")


if __name__ == "__main__":
    unittest.main()
