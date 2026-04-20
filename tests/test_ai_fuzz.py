"""AI 对战 Fuzz 测试：模拟人类随机操作 vs AI，验证所有场景下 AI 不崩溃。

通过真实 HTTP API 调用，覆盖：
- 换牌（0-3张随机）
- 初始部署（随机牌到随机阵地）
- 准备阶段
- 行动阶段（随机选操作：攻击/训练/征兵）
- 攻击决策（随机应答）
- 部署阶段（随机部署）
- AI 回合自动执行

运行: python tests/test_ai_fuzz.py [num_games]
"""

import json
import random
import sys
import threading
import time
import traceback
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from wargame.web import Handler, SESSION


def _post(base, path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{base}{path}", data=body,
                                headers={"Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=10)
    return json.loads(r.read())


def _get(base, path):
    return json.loads(urllib.request.urlopen(f"{base}{path}", timeout=10).read())


class AIFuzzTest(unittest.TestCase):
    """通过 HTTP 完整模拟人类 vs AI 对局。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 19876), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        cls.base = "http://127.0.0.1:19876"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _new_game(self, seed=None):
        payload = {
            "name1": "Human", "name2": "AI",
            "first_player": random.randint(0, 1),
            "ai_player": 1,
        }
        if seed is not None:
            payload["seed"] = seed
        return _post(self.base, "/api/new_game", payload)

    def _action(self, action, params=None):
        return _post(self.base, "/api/action",
                     {"action": action, "params": params or {}, "viewer": 0})

    def _ai_turn(self):
        return _post(self.base, "/api/ai_turn", {"viewer": 0})

    def _decision(self, answer):
        return _post(self.base, "/api/attack_decision", {"answer": answer, "viewer": 0})

    def _state(self):
        return _get(self.base, "/api/state?viewer=0")

    def _run_ai_if_needed(self, s):
        """如果轮到 AI，一直调 ai_turn 直到回到人类。"""
        max_iter = 10
        while s.get("is_ai_turn") and max_iter > 0:
            r = self._ai_turn()
            s = r.get("state", s)
            if r.get("game_over"):
                return s, True
            max_iter -= 1
        return s, False

    def _handle_pending(self, s):
        """处理攻击中的 pending 决策（随机应答）。"""
        max_iter = 20
        while s.get("pending_request") and max_iter > 0:
            req = s["pending_request"]
            t = req["type"]
            if t == "ace_choice":
                ans = random.choice([True, False])
            elif t == "spade_double_choice":
                ans = random.choice([True, False])
            elif t == "rescue_choice":
                ans = random.choice([None, 0])
            elif t == "continue_defense":
                ans = False
            elif t == "attack_target_choice":
                opts = req.get("options", ["back", "hq"])
                ans = random.choice(opts)
            elif t in ("hearts_draw", "diamonds_draw"):
                ans = random.randint(0, req.get("max", 0))
            else:
                ans = None
            r = self._decision(ans)
            s = r.get("state", s)
            if r.get("game_over"):
                return s, True
            if not r.get("pending"):
                break
            max_iter -= 1
        return s, False

    def _play_one_game(self, seed):
        """模拟一局完整的人类 vs AI 对局。返回 (winner, turns, error)。"""
        rng = random.Random(seed)

        r = self._new_game(seed=seed)
        s = r["state"]

        for turn_count in range(200):  # 防止无限循环
            if s.get("phase") == "game_over" or s.get("winner") is not None:
                return s.get("winner", -1), turn_count, None

            # AI 回合
            s, game_over = self._run_ai_if_needed(s)
            if game_over:
                return s.get("winner", -1), turn_count, None

            if s.get("phase") == "game_over":
                return s.get("winner", -1), turn_count, None

            # 人类回合
            phase = s.get("phase")

            if phase == "mulligan":
                # 随机换 0-3 张
                hand = s["players"][0].get("hand") or []
                n = min(rng.randint(0, 3), len(hand))
                indices = rng.sample(range(len(hand)), n) if n > 0 else []
                r = self._action("mulligan", {"indices": indices})
                s = r.get("state", s)
                s, game_over = self._run_ai_if_needed(s)
                if game_over:
                    return s.get("winner", -1), turn_count, None

            elif phase == "initial_deploy":
                hand = s["players"][0].get("hand") or []
                # 随机部署 0-5 张
                n = min(rng.randint(0, 5), len(hand))
                cards = hand[:n]
                placements = {}
                for c in cards:
                    z = rng.randint(0, 2)
                    placements.setdefault(str(z), []).append(c["id"])
                # 确保不超限
                for z in list(placements.keys()):
                    placements[z] = placements[z][:5]
                r = self._action("initial_deploy", {"placements": placements})
                if not r.get("ok"):
                    r = self._action("initial_deploy", {"placements": {}})
                s = r.get("state", s)
                s, game_over = self._run_ai_if_needed(s)
                if game_over:
                    return s.get("winner", -1), turn_count, None

            elif phase == "prepare":
                r = self._action("prepare", {"moves": {}})
                s = r.get("state", s)

            elif phase == "action":
                # 随机选操作
                available = s.get("available_actions", [])
                if not available or s.get("action_points", 0) <= 0:
                    r = self._action("end_action_phase")
                    s = r.get("state", s)
                else:
                    op = rng.choice(available)
                    if op == "attack":
                        # 找有牌的作战区
                        front = s["players"][0].get("front", [])
                        zones_with_cards = [z for z, t in enumerate(front) if t.get("size", 0) > 0]
                        if zones_with_cards:
                            zone = rng.choice(zones_with_cards)
                            r = self._action("attack", {"zone": zone})
                            s = r.get("state", s)
                            if r.get("pending"):
                                s, game_over = self._handle_pending(s)
                                if game_over:
                                    return s.get("winner", -1), turn_count, None
                        else:
                            r = self._action("end_action_phase")
                            s = r.get("state", s)
                    elif op == "train":
                        r = self._action("train")
                        s = r.get("state", s)
                    elif op == "recruit":
                        r = self._action("recruit")
                        s = r.get("state", s)
                    elif op == "reorganize":
                        # 跳过（复杂操作）
                        r = self._action("end_action_phase")
                        s = r.get("state", s)
                    elif op == "balance":
                        r = self._action("balance_graveyard", {"hand_indices": []})
                        s = r.get("state", s)
                    else:
                        r = self._action("end_action_phase")
                        s = r.get("state", s)

                    if not r.get("ok"):
                        # 操作失败，结束行动
                        r = self._action("end_action_phase")
                        s = r.get("state", s)

                if s.get("phase") == "game_over":
                    return s.get("winner", -1), turn_count, None

            elif phase == "deploy":
                hand = s["players"][0].get("hand") or []
                n = min(rng.randint(0, len(hand)), len(hand))
                cards = hand[:n]
                placements = {}
                for c in cards:
                    z = rng.randint(0, 2)
                    placements.setdefault(str(z), []).append(c["id"])
                for z in list(placements.keys()):
                    placements[z] = placements[z][:5]
                r = self._action("deploy", {"placements": placements})
                if not r.get("ok"):
                    r = self._action("deploy", {"placements": {}})
                s = r.get("state", s)

                # 结束回合
                if s.get("phase") != "game_over":
                    r = self._action("end_turn")
                    s = r.get("state", s)
                    s, game_over = self._run_ai_if_needed(s)
                    if game_over:
                        return s.get("winner", -1), turn_count, None

            else:
                # 未知阶段，刷新
                s = self._state()

        return -1, 200, "timeout"

    def test_fuzz_200_games(self):
        """运行 200 局随机 vs AI 对局，验证无崩溃。"""
        results = {"wins_human": 0, "wins_ai": 0, "draws": 0, "errors": 0}
        errors = []

        for i in range(200):
            try:
                winner, turns, err = self._play_one_game(seed=i * 7 + 42)
                if err:
                    results["errors"] += 1
                    errors.append(f"Game {i}: {err} after {turns} turns")
                elif winner == 0:
                    results["wins_human"] += 1
                elif winner == 1:
                    results["wins_ai"] += 1
                else:
                    results["draws"] += 1
            except Exception as e:
                results["errors"] += 1
                errors.append(f"Game {i}: EXCEPTION {type(e).__name__}: {e}")

        print(f"\n{'='*60}")
        print(f"Fuzz Test Results (200 games):")
        print(f"  Human wins: {results['wins_human']}")
        print(f"  AI wins:    {results['wins_ai']}")
        print(f"  Draws/TO:   {results['draws']}")
        print(f"  Errors:     {results['errors']}")
        if errors:
            print(f"\nErrors:")
            for e in errors[:10]:
                print(f"  {e}")
        print(f"{'='*60}")

        self.assertEqual(results["errors"], 0, f"AI crashed in {results['errors']} games: {errors[:5]}")
        completed = results["wins_human"] + results["wins_ai"]
        self.assertGreater(completed, 100, f"Only {completed}/200 games completed normally")


if __name__ == "__main__":
    # 可直接运行
    num = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    print(f"Running {num} fuzz games...")
    unittest.main(argv=[""], exit=True)
