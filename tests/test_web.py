"""Web API 端到端集成测试。

通过 HTTP 请求测试完整游戏流程，确保前端调用的所有接口可用。
"""

import json
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from wargame.web import Handler, SESSION, SpecialRules


def _post(url, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=5)
    return json.loads(r.read())


def _get(url):
    r = urllib.request.urlopen(url, timeout=5)
    return json.loads(r.read())


class WebIntegrationTests(unittest.TestCase):
    """使用真实 HTTP 服务器进行端到端测试。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 18765), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        cls.base = "http://127.0.0.1:18765"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _new_game(self, **kwargs):
        payload = {"name1": "P0", "name2": "P1", "first_player": 0}
        payload.update(kwargs)
        return _post(f"{self.base}/api/new_game", payload)

    def _action(self, action, params=None, viewer=0):
        return _post(f"{self.base}/api/action",
                     {"action": action, "params": params or {}, "viewer": viewer})

    def _decision(self, answer, viewer=0):
        return _post(f"{self.base}/api/attack_decision",
                     {"answer": answer, "viewer": viewer})

    def _state(self, viewer=0):
        return _get(f"{self.base}/api/state?viewer={viewer}")

    # ------------------------------------------------------------------
    # 基础连接
    # ------------------------------------------------------------------

    def test_homepage_returns_html(self):
        r = urllib.request.urlopen(f"{self.base}/", timeout=5)
        self.assertEqual(r.status, 200)
        html = r.read().decode()
        self.assertIn("Poker Battle", html)

    def test_static_css(self):
        r = urllib.request.urlopen(f"{self.base}/static/style.css", timeout=5)
        self.assertEqual(r.status, 200)

    def test_static_js(self):
        r = urllib.request.urlopen(f"{self.base}/static/app.js", timeout=5)
        self.assertEqual(r.status, 200)

    # ------------------------------------------------------------------
    # 新建对局
    # ------------------------------------------------------------------

    def test_new_game_creates_session(self):
        r = self._new_game()
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["phase"], "mulligan")
        self.assertEqual(r["state"]["players"][0]["hand_count"], 7)
        self.assertEqual(r["state"]["players"][0]["deck_count"], 13)
        self.assertEqual(r["state"]["graveyard_count"], 12)

    def test_new_game_with_special_rules(self):
        r = self._new_game(four_element=True, consecutive=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["state"]["special"]["four_element_defense"])
        self.assertTrue(r["state"]["special"]["consecutive_cards"])

    # ------------------------------------------------------------------
    # 信息隐藏
    # ------------------------------------------------------------------

    def test_opponent_hand_hidden(self):
        self._new_game()
        s = self._state(0)
        self.assertIsNotNone(s["players"][0]["hand"])
        self.assertIsNone(s["players"][1]["hand"])

    def test_opponent_zones_hidden(self):
        self._new_game()
        s = self._state(0)
        for zone in s["players"][1]["front"]:
            self.assertIsNone(zone["cards"])
        for zone in s["players"][1]["back"]:
            self.assertIsNone(zone["cards"])

    # ------------------------------------------------------------------
    # 完整回合流程
    # ------------------------------------------------------------------

    def test_full_turn_flow(self):
        """完整走一个开局+回合流程。"""
        self._new_game(seed=42)
        # 换牌
        r = self._action("mulligan", {"indices": []}, viewer=0)
        self.assertTrue(r["ok"])
        r = self._action("mulligan", {"indices": []}, viewer=1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["phase"], "initial_deploy")
        # 初始部署
        r = self._action("initial_deploy", {"placements": {}}, viewer=0)
        self.assertTrue(r["ok"])
        r = self._action("initial_deploy", {"placements": {}}, viewer=1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["phase"], "prepare")
        # 准备
        r = self._action("prepare", {"moves": {}}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["phase"], "action")
        self.assertEqual(r["state"]["action_points"], 1)
        # 征兵
        r = self._action("recruit", {}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["action_points"], 0)
        # 结束行动
        r = self._action("end_action_phase", {}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["phase"], "deploy")
        # 部署(空)
        r = self._action("deploy", {"placements": {}}, viewer=0)
        self.assertTrue(r["ok"])
        # 结束回合
        r = self._action("end_turn", {}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["current"], 1)
        self.assertEqual(r["state"]["phase"], "prepare")

    # ------------------------------------------------------------------
    # 非法操作
    # ------------------------------------------------------------------

    def test_wrong_phase_action(self):
        """在错误阶段执行操作返回错误。"""
        self._new_game()
        # 当前是换牌阶段，不能征兵
        r = self._action("recruit", {})
        self.assertFalse(r["ok"])

    def test_action_during_attack_blocked(self):
        """攻击结算中不能执行其他操作。"""
        self._new_game(seed=100)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        # 部署一张到待战区
        s = self._state(0)
        card_id = s["players"][0]["hand"][0]["id"]
        self._action("initial_deploy", {"placements": {"0": [card_id]}}, viewer=0)
        self._action("initial_deploy", {"placements": {}}, viewer=1)
        # 准备前移
        self._action("prepare", {"moves": {}}, viewer=0)
        # 发起攻击
        r = self._action("attack", {"zone": 0}, viewer=0)
        # 检查是否有 pending（可能直接完成也可能需要决策）
        if r.get("pending"):
            # 攻击中，其他操作应被阻止
            r2 = self._action("recruit", {}, viewer=0)
            self.assertFalse(r2["ok"])
            self.assertIn("攻击", r2["message"])

    # ------------------------------------------------------------------
    # 部署带具体牌
    # ------------------------------------------------------------------

    def test_deploy_specific_cards(self):
        """部署具体牌到待战区。"""
        self._new_game(seed=55)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        s = self._state(0)
        card_ids = [c["id"] for c in s["players"][0]["hand"][:2]]
        r = self._action("initial_deploy", {"placements": {"1": card_ids}}, viewer=0)
        self.assertTrue(r["ok"])
        s = self._state(0)
        self.assertEqual(s["players"][0]["back"][1]["size"], 2)
        self.assertEqual(s["players"][0]["hand_count"], 5)

    def test_deploy_invalid_card_fails(self):
        """部署不在手中的牌返回错误。"""
        self._new_game(seed=55)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        r = self._action("initial_deploy", {"placements": {"0": ["SPADES_ACE", "SPADES_ACE"]}}, viewer=0)
        # 至少一张不在手中
        # 可能 ok 也可能不 ok，取决于手里是否有这张牌
        # 用一张一定不存在的组合
        r = self._action("initial_deploy", {"placements": {"0": ["INVALID_X"]}}, viewer=0)
        self.assertFalse(r["ok"])

    # ------------------------------------------------------------------
    # 攻击决策完整流程
    # ------------------------------------------------------------------

    def test_attack_with_decisions(self):
        """攻击过程中的决策交互。"""
        self._new_game(seed=200)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        # P0 部署
        s = self._state(0)
        cards = [c["id"] for c in s["players"][0]["hand"][:3]]
        self._action("initial_deploy", {"placements": {"0": cards}}, viewer=0)
        # P1 部署
        s = self._state(1)
        cards1 = [c["id"] for c in s["players"][1]["hand"][:3]]
        self._action("initial_deploy", {"placements": {"0": cards1}}, viewer=1)
        # 准备
        self._action("prepare", {"moves": {}}, viewer=0)
        # 攻击
        r = self._action("attack", {"zone": 0}, viewer=0)
        self.assertTrue(r["ok"])
        # 处理所有 pending 决策
        max_iter = 20
        while r.get("pending") and max_iter > 0:
            req = r["state"]["pending_request"]
            if not req:
                break
            # 自动应答
            if req["type"] == "ace_choice":
                ans = True
            elif req["type"] == "spade_double_choice":
                ans = True
            elif req["type"] == "rescue_choice":
                ans = None
            elif req["type"] == "continue_defense":
                ans = False
            elif req["type"] == "attack_target_choice":
                ans = "back" if "back" in (req.get("options") or []) else "hq"
            elif req["type"] in ("hearts_draw", "diamonds_draw"):
                ans = req.get("max", 0)
            else:
                ans = None
            r = self._decision(ans, viewer=0)
            self.assertTrue(r["ok"])
            max_iter -= 1
        # 应该结算完毕
        self.assertFalse(r.get("pending", False))

    # ------------------------------------------------------------------
    # 制衡
    # ------------------------------------------------------------------

    def test_balance_graveyard_via_api(self):
        """通过 API 制衡（墓地模式）。"""
        self._new_game(seed=77)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        self._action("initial_deploy", {"placements": {}}, viewer=0)
        self._action("initial_deploy", {"placements": {}}, viewer=1)
        self._action("prepare", {"moves": {}}, viewer=0)
        # 制衡模式B
        r = self._action("balance_graveyard", {"hand_indices": [0]}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["action_points"], 0)

    # ------------------------------------------------------------------
    # 训练
    # ------------------------------------------------------------------

    def test_train_via_api(self):
        """训练操作增加手牌。"""
        self._new_game(seed=88)
        self._action("mulligan", {"indices": []}, viewer=0)
        self._action("mulligan", {"indices": []}, viewer=1)
        self._action("initial_deploy", {"placements": {}}, viewer=0)
        self._action("initial_deploy", {"placements": {}}, viewer=1)
        self._action("prepare", {"moves": {}}, viewer=0)
        s_before = self._state(0)
        hand_before = s_before["players"][0]["hand_count"]
        r = self._action("train", {}, viewer=0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["players"][0]["hand_count"], hand_before + 2)


if __name__ == "__main__":
    unittest.main()
