"""基于 1 亿局训练产出的冠军 AI 策略。

参数来源：10 阶段进化，最终 Elo 1960.0，胜率 68.8%。
所有决策仅基于玩家可见信息，无任何作弊。
"""

from __future__ import annotations
import random
from typing import Any, Dict, List

from .models import Card, Suit, Rank, Troop, NUM_ZONES, MAX_TROOP_SIZE
from .engine import Game, Phase, ActionKind, IllegalActionError


# 冠军参数（g8_cross_98808）
CHAMPION_PARAMS = {
    "w_attack": 10.54,
    "w_train": 1.16,
    "w_recruit": 5.71,
    "w_reorg": 1.02,
    "atk_min_value": 2.0,
    "atk_clubs_bonus": 6.0,
    "deck_panic": 5.27,
    "hand_hungry": 3.35,
    "late_aggro_turn": 4.09,
    "ace_high_atk": 0.95,
    "ace_high_def": 0.85,
    "spade_double": 1.0,
    "rescue_use": 0.72,
    "rescue_min_gap": 1.51,
    "bonus_fraction": 0.97,
    "deploy_ratio": 1.0,
    "deploy_clubs_focus": 0.83,
    "deploy_spread": 0.37,
    "prepare_all": 0.98,
    "continue_def": 0.11,
    "prefer_hq": 0.79,
    "early_recruit_bonus": 2.09,
    "mid_attack_bonus": 1.49,
}


class ChampionAI:
    """训练产出的冠军 AI，可作为 web 游戏对手。"""

    def __init__(self, player_idx: int, game: Game, params: Dict = None):
        self.idx = player_idx
        self.game = game
        self.p = params or CHAMPION_PARAMS
        self.rng = random.Random()

    @property
    def me(self):
        return self.game.players[self.idx]

    @property
    def opp(self):
        return self.game.players[1 - self.idx]

    # === 攻击/防御决策回调（用于 attack_steps 生成器）===

    def decide(self, req: Dict) -> Any:
        t = req["type"]
        if t == "ace_choice":
            ctx = req.get("context", "attack")
            prob = self.p["ace_high_atk"] if "attack" in ctx else self.p["ace_high_def"]
            return self.rng.random() < prob
        if t == "spade_double_choice":
            return self.rng.random() < self.p["spade_double"]
        if t == "rescue_choice":
            gap = req.get("gap", 0)
            if self.me.hand_size > 0 and self.rng.random() < self.p["rescue_use"]:
                if gap > self.p["rescue_min_gap"]:
                    return 0  # 打出手牌[0]
            return None
        if t == "continue_defense":
            return self.rng.random() < self.p["continue_def"]
        if t == "attack_target_choice":
            options = req.get("options", ["back", "hq"])
            if "hq" in options and self.rng.random() < self.p["prefer_hq"]:
                return "hq"
            return "back"
        if t in ("hearts_draw", "diamonds_draw"):
            mx = req.get("max", 0)
            return max(0, int(mx * self.p["bonus_fraction"]))
        return None

    # === 完整回合执行 ===

    def play_turn(self) -> List[Dict]:
        """执行 AI 的行动阶段，返回动作列表。不处理准备/部署/结束回合。"""
        actions = []
        g = self.game

        # 行动阶段
        if g.phase == Phase.ACTION:
            while g.action_points > 0 and g.phase == Phase.ACTION:
                op = self._choose_action()
                if op is None:
                    break
                if op == "attack":
                    zone = self._choose_attack_zone()
                    if zone is not None:
                        actions.append({"type": "attack", "zone": zone})
                        return actions  # 攻击需要生成器驱动
                    break
                elif op == "train":
                    try:
                        g.act_train()
                        actions.append({"type": "train"})
                    except (IllegalActionError, Exception):
                        break
                elif op == "recruit":
                    try:
                        g.act_recruit()
                        actions.append({"type": "recruit"})
                    except (IllegalActionError, Exception):
                        break
                elif op == "reorg":
                    done = self._do_reorg()
                    if done:
                        actions.append({"type": "reorg"})
                    else:
                        break
                else:
                    break

        return actions

    def do_mulligan(self) -> List[int]:
        """换牌：把非梅花低牌换掉。"""
        indices = []
        for i, c in enumerate(self.me.hand):
            if len(indices) >= 3:
                break
            if c.suit != Suit.CLUBS and c.base_value() <= 4:
                indices.append(i)
        return indices

    def do_initial_deploy(self) -> Dict[int, List[Card]]:
        """初始部署。"""
        return self._choose_deploy()

    # === 内部决策 ===

    def _choose_action(self) -> str | None:
        available = self.game.available_actions()
        if not available:
            return None

        turn = self.game.turn
        scores = {}

        # Attack
        if ActionKind.ATTACK in available:
            zone = self._choose_attack_zone()
            if zone is not None:
                val = self._estimate_attack_value(zone)
                if val >= self.p["atk_min_value"]:
                    mult = 1.0
                    if turn > self.p["late_aggro_turn"]:
                        mult += self.p["mid_attack_bonus"] * 0.3
                    scores["attack"] = self.p["w_attack"] * mult

        # Train
        if ActionKind.TRAIN in available and self.me.deck_size > 0:
            bonus = 3.0 if self.me.hand_size < self.p["hand_hungry"] else 0
            scores["train"] = self.p["w_train"] + bonus

        # Recruit
        if ActionKind.RECRUIT in available and len(self.game.graveyard) > 0:
            bonus = self.p["early_recruit_bonus"] + 2 if self.me.deck_size < self.p["deck_panic"] else 0
            if turn <= 3:
                bonus += self.p["early_recruit_bonus"]
            scores["recruit"] = self.p["w_recruit"] + bonus

        # Reorg
        if ActionKind.REORGANIZE in available:
            non_empty = sum(1 for z in range(NUM_ZONES) if not self.me.front[z].empty)
            if non_empty >= 2:
                scores["reorg"] = self.p["w_reorg"]

        if not scores:
            return None
        return max(scores, key=scores.get)

    def _choose_attack_zone(self) -> int | None:
        best_z, best_v = None, 0
        for z in range(NUM_ZONES):
            if self.me.front[z].empty:
                continue
            val = self._estimate_attack_value(z)
            if val > best_v:
                best_v = val
                best_z = z
        return best_z

    def _estimate_attack_value(self, zone: int) -> float:
        troop = self.me.front[zone]
        if troop.empty:
            return 0
        n = min(2, troop.size)
        cards = troop.cards[:n]
        first_clubs = (cards[0].suit == Suit.CLUBS)
        val = 0
        for c in cards:
            v = c.base_value(ace_as_high=True)
            if first_clubs and c.suit == Suit.CLUBS:
                v *= 2
            val += v
        if first_clubs:
            val += self.p["atk_clubs_bonus"]
        return val

    def _choose_deploy(self) -> Dict[int, List[Card]]:
        hand = list(self.me.hand)
        if not hand:
            return {}

        to_deploy = max(0, int(len(hand) * self.p["deploy_ratio"]))
        caps = [MAX_TROOP_SIZE - self.me.back[z].size for z in range(NUM_ZONES)]

        placements: Dict[int, List[Card]] = {}
        deployed = 0

        # 找梅花最多的阵地
        atk_z = max(range(NUM_ZONES), key=lambda z: caps[z])

        # 梅花优先到攻击阵地
        remaining = []
        for c in hand:
            if deployed >= to_deploy:
                break
            if c.suit == Suit.CLUBS and self.rng.random() < self.p["deploy_clubs_focus"] and caps[atk_z] > 0:
                placements.setdefault(atk_z, []).append(c)
                caps[atk_z] -= 1
                deployed += 1
            else:
                remaining.append(c)

        # 其余牌
        for c in remaining:
            if deployed >= to_deploy:
                break
            if self.p["deploy_spread"] > 0.5:
                tz = min(range(NUM_ZONES), key=lambda z: self.me.back[z].size + len(placements.get(z, [])))
            else:
                tz = max(range(NUM_ZONES), key=lambda z: caps[z])
            if caps[tz] <= 0:
                continue
            placements.setdefault(tz, []).append(c)
            caps[tz] -= 1
            deployed += 1

        return placements

    def _do_reorg(self) -> bool:
        # 找两个相邻非空作战区
        zones = [z for z in range(NUM_ZONES) if not self.me.front[z].empty]
        for i in range(len(zones) - 1):
            za, zb = zones[i], zones[i + 1]
            if abs(za - zb) == 1:
                pool = self.me.front[za].cards + self.me.front[zb].cards
                # 梅花集中到 za
                new_a = [c for c in pool if c.suit == Suit.CLUBS][:MAX_TROOP_SIZE]
                rest = [c for c in pool if c not in new_a]
                new_b = rest[:MAX_TROOP_SIZE]
                leftover = rest[MAX_TROOP_SIZE:]
                new_a = (new_a + leftover)[:MAX_TROOP_SIZE]
                try:
                    self.game.act_reorganize(za, zb, new_a, new_b)
                    return True
                except IllegalActionError:
                    return False
        return False
