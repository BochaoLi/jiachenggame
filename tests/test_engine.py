"""Poker Battle 1.8 核心规则测试 — 全覆盖版。"""

import unittest
from wargame.models import Card, Suit, Rank, Troop, PlayerState
from wargame.engine import (
    Game, Phase, ActionKind, SpecialRules,
    GameOverError, IllegalActionError, AttackReport,
)


# ============================================================================
# 测试用 Agent
# ============================================================================

class AutoAgent:
    """自动应答决策。默认：A=11，黑桃翻倍=是，奖励满抽，不急救，不继续翻。"""
    def __init__(self, **overrides):
        self.ov = overrides
    def decide(self, req):
        t = req["type"]
        if t == "ace_choice":
            return self.ov.get("ace_high", True)
        if t == "spade_double_choice":
            return self.ov.get("spade_double", True)
        if t == "rescue_choice":
            return self.ov.get("rescue", None)
        if t == "continue_defense":
            return self.ov.get("continue", False)
        if t == "attack_target_choice":
            return self.ov.get("target", "back")
        if t in ("hearts_draw", "diamonds_draw"):
            return req.get("max", 0)
        return None


class ScriptedAgent:
    """按脚本应答的 Agent。传入 answers 列表，依次消费。"""
    def __init__(self, answers):
        self.answers = list(answers)
        self.idx = 0
    def decide(self, req):
        if self.idx < len(self.answers):
            ans = self.answers[self.idx]
            self.idx += 1
            return ans
        return None


def _make_game(seed=42, **kwargs) -> Game:
    g = Game(("A", "B"), seed=seed, **kwargs)
    g.setup(first_player=0)
    return g


def _combat_game() -> Game:
    """创建一个已经过换牌和初始部署、可直接设置状态的游戏。"""
    g = Game(("A", "B"), seed=0)
    g.setup(0)
    g.mulligan([]); g.mulligan([])
    g.initial_deploy({}); g.initial_deploy({})
    return g


def _set_action(g: Game):
    """强制设置到行动阶段。"""
    g.phase = Phase.ACTION
    g.action_points = 2
    g.actions_used = []


# ============================================================================
# 1. SetupTests（初始化）
# ============================================================================

class SetupTests(unittest.TestCase):
    def test_initial_deal(self):
        g = _make_game()
        for p in g.players:
            self.assertEqual(p.hand_size, 7)
            self.assertEqual(p.deck_size, 13)
        self.assertEqual(len(g.graveyard), 12)
        self.assertEqual(g.phase, Phase.MULLIGAN)

    def test_mulligan_swap(self):
        g = _make_game()
        old_hand = list(g.players[0].hand)
        drawn = g.mulligan([0, 1])
        self.assertEqual(len(drawn), 2)
        self.assertEqual(g.players[0].hand_size, 7)
        # 被换出的牌不在手中
        self.assertNotIn(old_hand[0], g.players[0].hand)
        self.assertEqual(g.current, 1)
        g.mulligan([])
        self.assertEqual(g.phase, Phase.INITIAL_DEPLOY)

    def test_mulligan_max_three(self):
        g = _make_game()
        with self.assertRaises(IllegalActionError):
            g.mulligan([0, 1, 2, 3])  # 超过3张

    def test_initial_deploy(self):
        g = _make_game()
        g.mulligan([]); g.mulligan([])
        c = g.players[0].hand[0]
        g.initial_deploy({0: [c]})
        self.assertEqual(g.players[0].back[0].size, 1)
        self.assertEqual(g.players[0].hand_size, 6)
        g.initial_deploy({})
        self.assertEqual(g.phase, Phase.PREPARE)
        self.assertEqual(g.turn, 1)


# ============================================================================
# 2. PrepareTests（准备阶段）
# ============================================================================

class PrepareTests(unittest.TestCase):
    def _setup_to_prepare(self) -> Game:
        g = _make_game()
        g.mulligan([]); g.mulligan([])
        p0 = g.players[0]
        c1, c2 = p0.hand[0], p0.hand[1]
        g.initial_deploy({0: [c1, c2]})
        g.initial_deploy({})
        return g

    def test_prepare_moves_to_front(self):
        g = self._setup_to_prepare()
        g.do_prepare({})
        self.assertEqual(g.players[0].front[0].size, 2)
        self.assertEqual(g.players[0].back[0].size, 0)
        self.assertEqual(g.phase, Phase.ACTION)
        self.assertEqual(g.action_points, 1)

    def test_prepare_partial_move(self):
        g = self._setup_to_prepare()
        # 只移 1 张
        g.do_prepare({0: 1})
        self.assertEqual(g.players[0].front[0].size, 1)
        self.assertEqual(g.players[0].back[0].size, 1)

    def test_prepare_no_move(self):
        g = self._setup_to_prepare()
        g.do_prepare({0: 0})
        self.assertEqual(g.players[0].front[0].size, 0)
        self.assertEqual(g.players[0].back[0].size, 2)


# ============================================================================
# 3. ActionTests（行动阶段）
# ============================================================================

class ActionTests(unittest.TestCase):
    def _advance_to_action(self, g: Game, p0_deploy=None, p1_deploy=None):
        g.mulligan([]); g.mulligan([])
        g.initial_deploy(p0_deploy or {})
        g.initial_deploy(p1_deploy or {})
        g.do_prepare({})

    def test_recruit(self):
        g = _make_game()
        self._advance_to_action(g)
        before_deck = g.me.deck_size
        before_grave = len(g.graveyard)
        drawn = g.act_recruit()
        self.assertEqual(len(drawn), 2)
        self.assertEqual(g.me.deck_size, before_deck + 2)
        self.assertEqual(len(g.graveyard), before_grave - 2)

    def test_train(self):
        g = _make_game()
        self._advance_to_action(g)
        before_hand = g.me.hand_size
        before_deck = g.me.deck_size
        g.act_train()
        self.assertEqual(g.me.hand_size, before_hand + 2)
        self.assertEqual(g.me.deck_size, before_deck - 2)

    def test_no_repeat_action(self):
        g = _make_game()
        self._advance_to_action(g)
        g.act_recruit()
        with self.assertRaises(IllegalActionError):
            g.act_recruit()

    def test_first_turn_one_action(self):
        g = _make_game()
        self._advance_to_action(g)
        self.assertEqual(g.action_points, 1)
        g.act_recruit()
        with self.assertRaises(IllegalActionError):
            g.act_train()

    def test_second_turn_two_actions(self):
        g = _make_game()
        self._advance_to_action(g)
        g.act_recruit()
        g.end_action_phase()
        g.deploy({})
        g.end_turn()
        g.do_prepare({})
        g.act_recruit()
        g.end_action_phase()
        g.deploy({})
        g.end_turn()
        g.do_prepare({})
        self.assertEqual(g.action_points, 2)


# ============================================================================
# 4. BalanceTests（制衡操作）
# ============================================================================

class BalanceTests(unittest.TestCase):
    def test_balance_deck_swap(self):
        """制衡模式A：手牌↔牌库交换。"""
        g = _combat_game()
        _set_action(g)
        h0 = g.me.hand[0]
        d0 = g.me.deck[0]
        g.act_balance_deck([(0, 0)])
        self.assertEqual(g.me.hand[0], d0)
        self.assertEqual(g.me.deck[0], h0)

    def test_balance_deck_max_four(self):
        """制衡模式A最多交换4次。"""
        g = _combat_game()
        _set_action(g)
        with self.assertRaises(IllegalActionError):
            g.act_balance_deck([(0,0),(1,1),(2,2),(3,3),(4,4)])

    def test_balance_graveyard(self):
        """制衡模式B：手牌↔墓地交换。"""
        g = _combat_game()
        _set_action(g)
        before_hand = set(c for c in g.me.hand)
        h0_idx = 0
        drawn = g.act_balance_graveyard([h0_idx])
        self.assertEqual(len(drawn), 1)
        self.assertEqual(g.me.hand_size, 7)

    def test_balance_graveyard_max_two(self):
        """制衡模式B最多2张。"""
        g = _combat_game()
        _set_action(g)
        with self.assertRaises(IllegalActionError):
            g.act_balance_graveyard([0, 1, 2])


# ============================================================================
# 5. HQPenaltyValueTests（大本营惩罚点数）
# ============================================================================

class HQPenaltyValueTests(unittest.TestCase):
    def test_penalty_values(self):
        self.assertEqual(Card(Suit.SPADES, Rank.ACE).hq_penalty_value(), 1)
        self.assertEqual(Card(Suit.HEARTS, Rank.TEN).hq_penalty_value(), 5)
        self.assertEqual(Card(Suit.CLUBS, Rank.JACK).hq_penalty_value(), 5)
        self.assertEqual(Card(Suit.DIAMONDS, Rank.KING).hq_penalty_value(), 5)
        self.assertEqual(Card(Suit.SPADES, Rank.SEVEN).hq_penalty_value(), 7)
        self.assertEqual(Card(Suit.HEARTS, Rank.TWO).hq_penalty_value(), 2)

    def test_discard_x_values(self):
        self.assertEqual(Card(Suit.HEARTS, Rank.TWO).discard_x(), 3)
        self.assertEqual(Card(Suit.HEARTS, Rank.FIVE).discard_x(), 3)
        self.assertEqual(Card(Suit.DIAMONDS, Rank.SIX).discard_x(), 2)
        self.assertEqual(Card(Suit.DIAMONDS, Rank.NINE).discard_x(), 2)
        self.assertEqual(Card(Suit.HEARTS, Rank.TEN).discard_x(), 1)
        self.assertEqual(Card(Suit.DIAMONDS, Rank.KING).discard_x(), 1)
        self.assertEqual(Card(Suit.HEARTS, Rank.ACE).discard_x(False), 4)  # A=1
        self.assertEqual(Card(Suit.HEARTS, Rank.ACE).discard_x(True), 1)   # A=11


# ============================================================================
# 6. AttackTests（攻击结算 — 基础）
# ============================================================================

class AttackTests(unittest.TestCase):
    def test_clubs_first_card_doubles(self):
        """第一张是梅花时，所有梅花牌翻倍。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.FIVE), Card(Suit.CLUBS, Rank.THREE)])
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.KING), Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        self.assertEqual(report.total_attack, 16)  # (5+3)*2
        self.assertTrue(report.clubs_doubled)
        self.assertTrue(report.defense_held)

    def test_clubs_second_not_doubled(self):
        """第一张不是梅花时，第二张梅花不翻倍。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.FIVE), Card(Suit.CLUBS, Rank.THREE)])
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.NINE)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        self.assertEqual(report.total_attack, 8)  # 5+3 no double
        self.assertFalse(report.clubs_doubled)

    def test_hq_defense_penalty(self):
        """大本营防御惩罚：A=1, 10JQK=5。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.FIVE)])  # 10
        g.players[1].front[0] = Troop()
        g.players[1].back[0] = Troop()
        g.players[1].deck = [
            Card(Suit.HEARTS, Rank.ACE),      # 1
            Card(Suit.SPADES, Rank.KING),      # 5
            Card(Suit.DIAMONDS, Rank.QUEEN),   # 5
            Card(Suit.SPADES, Rank.TWO),       # 2 (spare)
        ]
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertTrue(report.defense_held)
        self.assertEqual(report.defense_cards[0].value, 1)
        self.assertEqual(report.defense_cards[1].value, 5)
        self.assertEqual(report.defense_cards[2].value, 5)

    def test_hq_defense_defeat(self):
        """大本营牌库耗尽→败北。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[1].front[0] = Troop()
        g.players[1].back[0] = Troop()
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)]  # only 2 points
        with self.assertRaises(GameOverError) as ctx:
            g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertEqual(ctx.exception.winner_idx, 0)

    def test_troop_destroyed_skips_discard(self):
        """被击毁的部队跳过弃牌效果。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2, destroyed
        g.players[1].back[0] = Troop()
        g.players[1].deck = [Card(Suit.SPADES, Rank.NINE)] * 8
        before_hand = g.players[1].hand_size
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertTrue(report.troop_destroyed)
        self.assertEqual(g.players[1].hand_size, before_hand)

    def test_silenced_blocks_attacker_discard(self):
        """沉默：首张防御黑桃且黑桃总和≥攻击→攻击方弃牌无效。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.THREE)])  # 3
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.FIVE)])  # 5*2=10 >= 3
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        self.assertTrue(report.silenced)
        self.assertEqual(g.players[0].hand_size, before_hand)

    def test_reorganize_adjacent_only(self):
        """重编只能选相邻作战区。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.SPADES, Rank.TWO)])
        g.players[0].front[2] = Troop([Card(Suit.HEARTS, Rank.THREE)])
        with self.assertRaises(IllegalActionError):
            g.act_reorganize(0, 2, [], [])

    def test_spade_double_optional(self):
        """黑桃翻倍是可选的。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.SEVEN)])  # 7
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.FOUR)])  # 选不翻倍=4 < 7
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # 不翻倍：4 < 7，需要继续翻 hq
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        # 不翻倍时 4 < 7，部队被击毁
        self.assertTrue(report.troop_destroyed)


# ============================================================================
# 7. OverflowTests（溢出攻击）
# ============================================================================

class OverflowTests(unittest.TestCase):
    def test_overflow_to_hq(self):
        """击毁作战区后溢出到大本营。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.TWO)])  # 2*2=4
        g.players[1].back[0] = Troop()
        # 溢出 20-4=16 打大本营
        g.players[1].deck = [Card(Suit.SPADES, Rank.NINE)] * 6  # 每张 9 点(惩罚下)
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertTrue(report.troop_destroyed)
        self.assertEqual(report.overflow, 16)
        self.assertIsNotNone(report.overflow_report)
        self.assertEqual(report.overflow_report.target_type, "hq")
        self.assertTrue(report.overflow_report.defense_held)

    def test_overflow_to_back(self):
        """击毁作战区后溢出到待战区。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.TEN)])  # 20
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2
        # 待战区有牌
        g.players[1].back[0] = Troop([Card(Suit.SPADES, Rank.KING), Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # 溢出 20-2=18 打待战区 K(10)+K(10)=20>=18 守住
        report = g.op_attack(0, AutoAgent(target="back"), AutoAgent(spade_double=False))
        self.assertTrue(report.troop_destroyed)
        self.assertEqual(report.overflow, 18)
        self.assertIsNotNone(report.overflow_report)
        self.assertEqual(report.overflow_report.target_type, "back")
        self.assertTrue(report.overflow_report.defense_held)

    def test_overflow_defeats_player(self):
        """溢出攻击耗尽大本营→败北。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2
        g.players[1].back[0] = Troop()
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)]  # 溢出18 vs 2(hq惩罚)→死
        with self.assertRaises(GameOverError) as ctx:
            g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertEqual(ctx.exception.winner_idx, 0)


# ============================================================================
# 8. RescueTests（红桃急救）
# ============================================================================

class RescueTests(unittest.TestCase):
    def test_rescue_adds_defense(self):
        """急救：翻出红桃时从手牌打出1张加入防御。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.FIVE)])  # 10
        # 防御方阵地：红桃3(3) → 触发急救，手牌打出黑桃K(10)
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.THREE)])
        g.players[1].hand = [Card(Suit.SPADES, Rank.KING)] + g.players[1].hand
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        # Agent: 翻红桃时选择打出手牌索引0（黑桃K）
        # 3 + 10(rescue) = 13 >= 10, 防住
        def_agent = ScriptedAgent([
            False,   # spade_double for hearts3? no, it's hearts
            0,       # rescue: 打出手牌[0]
            True,    # ace_choice for rescue card? no, K is not ace
            True,    # spade_double for K
        ])
        # 更好的方案：用简单 Agent
        class RescueAgent:
            def decide(self, req):
                if req["type"] == "rescue_choice": return 0  # 打手牌[0]
                if req["type"] == "spade_double_choice": return True
                if req["type"] == "ace_choice": return True
                if req["type"] == "continue_defense": return False
                if req["type"] in ("hearts_draw","diamonds_draw"): return req.get("max",0)
                return None
        report = g.op_attack(0, AutoAgent(), RescueAgent())
        self.assertTrue(report.rescue_used)
        self.assertTrue(report.defense_held)

    def test_rescue_limit_once(self):
        """急救每轮限一次。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.NINE)])  # 18
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10
        # 防御方：红桃2(2) + 红桃3(3) + 黑桃K(20) → 第一次红桃触发急救
        g.players[1].front[0] = Troop([
            Card(Suit.HEARTS, Rank.TWO),
            Card(Suit.HEARTS, Rank.THREE),
            Card(Suit.SPADES, Rank.KING),
        ])
        g.players[1].hand = [Card(Suit.SPADES, Rank.FIVE)] * 3
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        class AlwaysRescueAgent:
            def decide(self, req):
                if req["type"] == "rescue_choice": return 0
                if req["type"] == "spade_double_choice": return True
                if req["type"] == "ace_choice": return True
                if req["type"] == "continue_defense": return False
                if req["type"] in ("hearts_draw","diamonds_draw"): return req.get("max",0)
                return None
        report = g.op_attack(0, AutoAgent(), AlwaysRescueAgent())
        # 应该只有一次急救触发（第一张红桃触发，第二张不再触发）
        rescue_count = sum(1 for e in report.defense_cards if e.source == "hand_rescue")
        self.assertEqual(rescue_count, 1)


# ============================================================================
# 9. DiscardEffectTests（弃牌效果）
# ============================================================================

class DiscardEffectTests(unittest.TestCase):
    def test_hearts_discard_draws_from_deck(self):
        """红桃弃牌效果：牌库→手牌。"""
        g = _combat_game(); _set_action(g)
        # 攻击用红桃5（第一张=红桃→弃牌效果生效）
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.FIVE)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        # 防御用红桃K(10)，第一张不是黑桃→不触发沉默
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        before_deck = g.players[0].deck_size
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        self.assertFalse(report.silenced)
        # 红桃5 X=3，满抽
        self.assertEqual(g.players[0].hand_size, before_hand + 3)
        self.assertEqual(g.players[0].deck_size, before_deck - 3)

    def test_diamonds_discard_draws_from_graveyard(self):
        """方片弃牌效果：墓地→牌库。"""
        g = _combat_game(); _set_action(g)
        # 攻击用方片4（第一张=方片→弃牌效果生效）
        g.players[0].front[0] = Troop([Card(Suit.DIAMONDS, Rank.FOUR)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # 防御用红桃K(10>=4)，不是黑桃→不沉默
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_deck = g.players[0].deck_size
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        self.assertFalse(report.silenced)
        # 方片4 X=3
        self.assertEqual(g.players[0].deck_size, before_deck + 3)

    def test_discard_first_suit_rule_blocks(self):
        """攻击方第一张非红桃→第二张红桃不触发弃牌效果。"""
        g = _combat_game(); _set_action(g)
        # 第一张梅花，第二张红桃 → 红桃不触发弃牌
        g.players[0].front[0] = Troop([
            Card(Suit.CLUBS, Rank.THREE),   # 第一张=梅花
            Card(Suit.HEARTS, Rank.FIVE),   # 第二张=红桃，不触发
        ])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.KING), Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        # 梅花翻倍生效，但红桃弃牌效果不执行（第一张不是红桃）
        self.assertTrue(report.clubs_doubled)
        self.assertEqual(g.players[0].hand_size, before_hand)

    def test_x_cap_at_six(self):
        """多张同花色弃牌 X 加和上限为 6。"""
        g = _combat_game(); _set_action(g)
        # 两张红桃(X=3+3=6, cap=6), 第一张是红桃→生效
        g.players[0].front[0] = Troop([
            Card(Suit.HEARTS, Rank.TWO),   # X=3
            Card(Suit.HEARTS, Rank.THREE), # X=3
        ])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10
        # 防御用梅花K(10)→不是黑桃不沉默
        g.players[1].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        self.assertFalse(report.silenced)
        # X=3+3=6, 满抽6
        self.assertEqual(g.players[0].hand_size, before_hand + 6)

    def test_defense_first_suit_rule(self):
        """防御方第一张非红桃→后续红桃不触发弃牌效果。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.NINE)])  # 18
        # 防御：黑桃3(6) + 红桃K(10) = 16 < 18... 需要多一张
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.THREE),  # 第一张=黑桃
            Card(Suit.HEARTS, Rank.KING),   # 红桃，但第一张是黑桃→弃牌不触发
            Card(Suit.SPADES, Rank.TWO),    # 2*2=4
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        before_hand = g.players[1].hand_size
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        # 防御方红桃K不触发弃牌（第一张是黑桃）
        self.assertEqual(g.players[1].hand_size, before_hand)


# ============================================================================
# 10. ContinueDefenseTests（防御方选择继续翻牌）
# ============================================================================

class ContinueDefenseTests(unittest.TestCase):
    def test_continue_flipping(self):
        """防御成功后可选择继续翻牌。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2
        # 防御：黑桃K(20) → 已防住，但可选择继续翻
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.KING),   # 20 >= 2, 防住
            Card(Suit.HEARTS, Rank.FIVE),   # 继续翻
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # Agent 选择继续翻一次
        class ContinueOnceAgent:
            def __init__(self):
                self.cont_count = 0
            def decide(self, req):
                if req["type"] == "continue_defense":
                    self.cont_count += 1
                    return self.cont_count <= 1  # 只继续一次
                if req["type"] == "spade_double_choice": return True
                if req["type"] == "ace_choice": return True
                if req["type"] in ("hearts_draw","diamonds_draw"): return req.get("max",0)
                if req["type"] == "rescue_choice": return None
                return None
        report = g.op_attack(0, AutoAgent(), ContinueOnceAgent())
        self.assertTrue(report.defense_held)
        self.assertEqual(len(report.defense_cards), 2)  # 翻了2张


# ============================================================================
# 11. AttackTargetTests（攻击目标选择）
# ============================================================================

class AttackTargetTests(unittest.TestCase):
    def test_must_attack_front_if_present(self):
        """对应作战区有部队时必须攻击作战区。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.TWO)])  # 4
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.KING)])  # 有部队
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        self.assertEqual(report.target_type, "front")

    def test_choose_hq_when_front_empty(self):
        """作战区空时可选择攻击大本营。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2
        g.players[1].front[0] = Troop()  # 空
        g.players[1].back[0] = Troop([Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertEqual(report.target_type, "hq")

    def test_choose_back_when_front_empty(self):
        """作战区空时可选择攻击待战区。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])  # 2
        g.players[1].front[0] = Troop()
        g.players[1].back[0] = Troop([Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        report = g.op_attack(0, AutoAgent(target="back"), AutoAgent())
        self.assertEqual(report.target_type, "back")


# ============================================================================
# 12. FourElementDefenseTests（四象防御特殊规则）
# ============================================================================

class FourElementDefenseTests(unittest.TestCase):
    def test_four_suits_instant_defense(self):
        """四花色齐全时立即防御成功。"""
        g = Game(("A","B"), seed=0, special_rules=SpecialRules(four_element_defense=True))
        g.setup(0); g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10  # 足够deck
        # 防御方有 4 种花色的牌（点数不够 20，但四象触发）
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.TWO),    # 2*2=4
            Card(Suit.HEARTS, Rank.TWO),    # 2
            Card(Suit.CLUBS, Rank.TWO),     # 2
            Card(Suit.DIAMONDS, Rank.TWO),  # 2 → 四花色齐，立即成功
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=True))
        self.assertTrue(report.four_element_triggered)
        self.assertTrue(report.defense_held)
        self.assertFalse(report.troop_destroyed)

    def test_four_element_off_by_default(self):
        """默认不启用四象防御。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.TWO),
            Card(Suit.HEARTS, Rank.TWO),
            Card(Suit.CLUBS, Rank.TWO),
            Card(Suit.DIAMONDS, Rank.TWO),
        ])
        g.players[1].back[0] = Troop()
        g.players[1].deck = [Card(Suit.SPADES, Rank.NINE)] * 6  # 足够抗溢出
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent(spade_double=True))
        # 没有四象规则，4+2+2+2=10 < 20，部队被击毁
        self.assertFalse(report.four_element_triggered)
        self.assertTrue(report.troop_destroyed)


# ============================================================================
# 13. DeployTests（部署）
# ============================================================================

class DeployTests(unittest.TestCase):
    def test_deploy_to_back(self):
        g = _make_game()
        g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        g.do_prepare({})
        g.end_action_phase()
        c = g.me.hand[0]
        g.deploy({1: [c]})
        self.assertEqual(g.me.back[1].size, 1)
        self.assertEqual(g.me.back[1].cards[0], c)

    def test_deploy_exceeds_max(self):
        """部署超过5张报错。"""
        g = _make_game()
        g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        g.do_prepare({})
        g.end_action_phase()
        # 试图放6张到同一个待战区
        cards = g.me.hand[:6]
        with self.assertRaises(IllegalActionError):
            g.deploy({0: cards})

    def test_deploy_card_not_in_hand(self):
        """部署不在手中的牌报错。"""
        g = _make_game()
        g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        g.do_prepare({})
        g.end_action_phase()
        fake = Card(Suit.SPADES, Rank.ACE)
        if fake not in g.me.hand:
            with self.assertRaises(IllegalActionError):
                g.deploy({0: [fake]})


# ============================================================================
# 14. FullGameFlowTest（完整回合端到端）
# ============================================================================

class FullGameFlowTest(unittest.TestCase):
    def test_full_turn_cycle(self):
        """验证完整的多回合游戏流程不会崩溃。"""
        g = _make_game(seed=99)
        g.mulligan([0]); g.mulligan([])
        c = g.players[0].hand[0]
        g.initial_deploy({0: [c]})
        g.initial_deploy({})
        g.do_prepare({})
        self.assertEqual(g.action_points, 1)
        g.act_recruit()
        g.end_action_phase()
        g.deploy({})
        g.end_turn()
        g.do_prepare({})
        self.assertEqual(g.action_points, 2)
        g.act_train()
        g.act_recruit()
        g.end_action_phase()
        if g.me.hand:
            g.deploy({1: [g.me.hand[0]]})
        else:
            g.deploy({})
        g.end_turn()
        g.do_prepare({})
        self.assertEqual(g.action_points, 2)
        self.assertEqual(g.phase, Phase.ACTION)

    def test_game_over_detection(self):
        """牌库耗尽触发游戏结束。"""
        g = _combat_game(); _set_action(g)
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO), Card(Suit.SPADES, Rank.THREE)]
        with self.assertRaises(GameOverError):
            g.act_train()


# ============================================================================
# 15. ReorganizeTests（重编正确路径）
# ============================================================================

class ReorganizeTests(unittest.TestCase):
    def test_reorganize_success(self):
        """重编成功重新分配。"""
        g = _combat_game(); _set_action(g)
        a = Card(Suit.SPADES, Rank.TWO)
        b = Card(Suit.HEARTS, Rank.THREE)
        c = Card(Suit.CLUBS, Rank.FOUR)
        g.players[0].front[0] = Troop([a, b])
        g.players[0].front[1] = Troop([c])
        g.act_reorganize(0, 1, [c, a], [b])
        self.assertEqual(g.players[0].front[0].cards, [c, a])
        self.assertEqual(g.players[0].front[1].cards, [b])

    def test_reorganize_preserves_cards(self):
        """重编不能偷牌或丢牌。"""
        g = _combat_game(); _set_action(g)
        a = Card(Suit.SPADES, Rank.TWO)
        b = Card(Suit.HEARTS, Rank.THREE)
        g.players[0].front[0] = Troop([a])
        g.players[0].front[1] = Troop([b])
        fake = Card(Suit.DIAMONDS, Rank.KING)
        with self.assertRaises(IllegalActionError):
            g.act_reorganize(0, 1, [a, fake], [])


# ============================================================================
# 16. SilenceEdgeCases（沉默边界情况）
# ============================================================================

class SilenceEdgeCases(unittest.TestCase):
    def test_silence_not_triggered_if_spade_sum_less(self):
        """黑桃总和 < 攻击时不触发沉默。"""
        g = _combat_game(); _set_action(g)
        # 攻击：红桃9(9)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.NINE)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        # 防御：黑桃2(2*2=4) + 黑桃3(3*2=6) = 10 >= 9, 但黑桃点数之和计入防守时
        # 沉默要求：黑桃总和 >= 攻击(9)。黑桃总和=4+6=10>=9 → 会沉默
        # 改为：攻击=12，黑桃2(4)+红桃K(10)=14>=12防住，但黑桃只有4<12 → 不沉默
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.SIX)])  # 12
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.TWO),   # 首张黑桃, 4
            Card(Suit.HEARTS, Rank.KING),  # 10
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        # 黑桃总和=4 < 12，不沉默
        self.assertFalse(report.silenced)

    def test_silence_only_when_first_is_spade(self):
        """首张不是黑桃时不触发沉默（即使后面有黑桃总和够）。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.THREE)])  # 3
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # 防御：红桃2(2) 首张 + 黑桃K(20) → 第一张不是黑桃 → 不沉默
        g.players[1].front[0] = Troop([
            Card(Suit.HEARTS, Rank.TWO),
            Card(Suit.SPADES, Rank.KING),
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=True))
        self.assertFalse(report.silenced)


# ============================================================================
# 17. HQDefenseNoEffects（大本营防守无花色效果）
# ============================================================================

class HQDefenseNoEffects(unittest.TestCase):
    def test_hq_hearts_no_draw(self):
        """大本营翻出红桃不触发弃牌效果。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.TWO)])  # 4
        g.players[1].front[0] = Troop()
        g.players[1].back[0] = Troop()
        g.players[1].deck = [
            Card(Suit.HEARTS, Rank.FIVE),  # 惩罚点5
            Card(Suit.SPADES, Rank.TWO),  # spare
            Card(Suit.SPADES, Rank.TWO),
        ]
        before_hand = g.players[1].hand_size
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        # 大本营翻出红桃5不触发弃牌效果
        self.assertEqual(g.players[1].hand_size, before_hand)
        self.assertEqual(report.target_type, "hq")

    def test_hq_spade_no_double(self):
        """大本营翻出黑桃不享受×2。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.SIX)])  # 6
        g.players[1].front[0] = Troop()
        g.players[1].back[0] = Troop()
        # 黑桃K 惩罚点=5, 不是10也不是20
        g.players[1].deck = [
            Card(Suit.SPADES, Rank.KING),  # 5
            Card(Suit.SPADES, Rank.THREE), # 3 → 合计 8>=6
            Card(Suit.SPADES, Rank.TWO),
        ]
        report = g.op_attack(0, AutoAgent(target="hq"), AutoAgent())
        self.assertTrue(report.defense_held)
        # 黑桃K在大本营计为5不是10也不是20
        self.assertEqual(report.defense_cards[0].value, 5)


# ============================================================================
# 18. GraveyardTests（墓地清理）
# ============================================================================

class GraveyardTests(unittest.TestCase):
    def test_all_combat_cards_go_to_graveyard(self):
        """攻防所有牌进入墓地。"""
        g = _combat_game(); _set_action(g)
        atk_card = Card(Suit.CLUBS, Rank.FIVE)
        def_card = Card(Suit.SPADES, Rank.KING)
        g.players[0].front[0] = Troop([atk_card])
        g.players[1].front[0] = Troop([def_card])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_grave = len(g.graveyard)
        report = g.op_attack(0, AutoAgent(), AutoAgent())
        # 2张牌(1攻+1防)应该进入墓地
        self.assertGreaterEqual(len(g.graveyard), before_grave + 2)
        # 攻击牌和防御牌都不再在阵地
        self.assertEqual(g.players[0].front[0].size, 0)
        self.assertEqual(g.players[1].front[0].size, 0)


# ============================================================================
# 19. OverflowNoSilenceTests（溢出待战区黑桃不沉默）
# ============================================================================

class OverflowNoSilenceTests(unittest.TestCase):
    def test_overflow_back_spade_no_silence(self):
        """溢出攻击待战区时黑桃不执行沉默效果。"""
        g = _combat_game(); _set_action(g)
        # 攻击：梅花K(20)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        # 作战区：红桃2(2)被击毁，溢出18
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])
        # 待战区：黑桃K首张(翻倍20>=18)，正常会沉默，但溢出时不执行沉默
        g.players[1].back[0] = Troop([Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(target="back"), AutoAgent())
        self.assertTrue(report.troop_destroyed)
        self.assertIsNotNone(report.overflow_report)
        # 溢出攻击待战区时不应触发沉默
        self.assertFalse(report.overflow_report.silenced)


# ============================================================================
# 20. DiscardOrderTests（弃牌效果执行顺序）
# ============================================================================

class DiscardOrderTests(unittest.TestCase):
    def test_attacker_discard_executes_first(self):
        """弃牌效果从攻方先执行。"""
        g = _combat_game(); _set_action(g)
        # 攻击：红桃3(3), 第一张红桃→攻方弃牌效果生效
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.THREE)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        # 防御：红桃K(10>=3), 第一张红桃→防方弃牌效果也生效
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        # 双方都触发了弃牌效果
        atk_effects = [e for e in report.discard_effects if e.player == 0]
        def_effects = [e for e in report.discard_effects if e.player == 1]
        self.assertTrue(len(atk_effects) > 0)
        self.assertTrue(len(def_effects) > 0)
        # 攻方效果在前
        all_players = [e.player for e in report.discard_effects]
        first_atk = all_players.index(0) if 0 in all_players else 999
        first_def = all_players.index(1) if 1 in all_players else 999
        self.assertLess(first_atk, first_def)


# ============================================================================
# 21. RescueNoDiscard（急救触发的红桃不执行弃牌效果）
# ============================================================================

class RescueNoDiscardTests(unittest.TestCase):
    def test_rescue_hearts_no_discard_effect(self):
        """触发急救的红桃牌无法触发弃牌效果。"""
        g = _combat_game(); _set_action(g)
        # 攻击：梅花3(6)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.THREE)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # 防御：红桃5(5) → 触发急救，打出黑桃2(2*2=4) → 总防5+4=9>=6
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.FIVE)])
        g.players[1].hand = [Card(Suit.SPADES, Rank.TWO)] + g.players[1].hand
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        before_deck = g.players[1].deck_size
        class UseRescueAgent:
            def decide(self, req):
                if req["type"] == "rescue_choice": return 0
                if req["type"] == "spade_double_choice": return True
                if req["type"] == "ace_choice": return True
                if req["type"] == "continue_defense": return False
                if req["type"] in ("hearts_draw","diamonds_draw"): return req.get("max",0)
                return None
        report = g.op_attack(0, AutoAgent(), UseRescueAgent())
        self.assertTrue(report.rescue_used)
        # 触发急救的红桃5不应执行弃牌效果(牌库→手牌)
        # 如果执行了，deck_size会减少；不执行则不变
        self.assertEqual(g.players[1].deck_size, before_deck)


# ============================================================================
# 22. FourElementCombinationTests（四象防御组合）
# ============================================================================

class FourElementCombinationTests(unittest.TestCase):
    def test_four_element_all_discard_effects(self):
        """四象触发时所有红桃方片执行弃牌效果（无视第一张规则）。"""
        g = Game(("A","B"), seed=0, special_rules=SpecialRules(four_element_defense=True))
        g.setup(0); g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 20
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10
        # 防御：4花色触发四象。第一张是黑桃，但红桃方片仍触发弃牌
        g.players[1].front[0] = Troop([
            Card(Suit.SPADES, Rank.TWO),    # 首张黑桃
            Card(Suit.HEARTS, Rank.THREE),  # 红桃 X=3
            Card(Suit.CLUBS, Rank.TWO),
            Card(Suit.DIAMONDS, Rank.FOUR), # 方片 X=3
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        before_hand = g.players[1].hand_size
        before_deck = g.players[1].deck_size
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=True))
        self.assertTrue(report.four_element_triggered)
        # 红桃弃牌效果执行了（牌库→手牌），即使首张是黑桃
        self.assertGreater(g.players[1].hand_size, before_hand)


# ============================================================================
# 23. WebAPITests（网页 API 端到端）
# ============================================================================

class WebAPITests(unittest.TestCase):
    """通过直接调用 web 层函数测试 API 逻辑。"""

    def setUp(self):
        from wargame.web import SESSION, SpecialRules as SR
        SESSION.reset(("P0", "P1"), seed=42, first=0,
                      special=SR(four_element_defense=False, consecutive_cards=False))
        self.session = SESSION

    def test_new_game_state(self):
        from wargame.web import serialize_state
        s = serialize_state(0)
        self.assertTrue(s["started"])
        self.assertEqual(s["phase"], "mulligan")
        self.assertEqual(s["players"][0]["hand_count"], 7)
        self.assertEqual(s["players"][0]["deck_count"], 13)
        # 自己的手牌可见
        self.assertIsNotNone(s["players"][0]["hand"])
        # 对手手牌不可见
        self.assertIsNone(s["players"][1]["hand"])

    def test_action_mulligan(self):
        from wargame.web import _do_action, serialize_state
        r = _do_action("mulligan", {"indices": [0, 1]})
        self.assertTrue(r["ok"])
        s = serialize_state(1)
        self.assertEqual(s["phase"], "mulligan")  # 还在换牌（P1还没换）
        r = _do_action("mulligan", {"indices": []})
        self.assertTrue(r["ok"])
        s = serialize_state(0)
        self.assertEqual(s["phase"], "initial_deploy")

    def test_action_during_attack_blocked(self):
        from wargame.web import SESSION, _do_action, serialize_state
        g = SESSION.game
        g.mulligan([]); g.mulligan([])
        g.initial_deploy({}); g.initial_deploy({})
        g.do_prepare({})
        g.phase = Phase.ACTION; g.action_points = 2; g.actions_used = []
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.FIVE)])
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        # 发起攻击
        r = _do_action("attack", {"zone": 0})
        # 攻击进行中其他操作应被阻止
        r2 = _do_action("recruit", {})
        self.assertFalse(r2["ok"])
        self.assertIn("攻击", r2["message"])

    def test_card_from_id_roundtrip(self):
        from wargame.web import _card, _card_from_id
        c = Card(Suit.HEARTS, Rank.ACE)
        serialized = _card(c)
        restored = _card_from_id(serialized["id"])
        self.assertEqual(restored, c)

    def test_serialize_hides_opponent_info(self):
        from wargame.web import serialize_state
        s = serialize_state(0)
        # 对手的 front/back 的 cards 为 None
        for zone in s["players"][1]["front"]:
            self.assertIsNone(zone["cards"])
        for zone in s["players"][1]["back"]:
            self.assertIsNone(zone["cards"])

    def test_illegal_action_returns_error(self):
        from wargame.web import _do_action
        # 当前是换牌阶段，不能征兵
        r = _do_action("recruit", {})
        self.assertFalse(r["ok"])


# ============================================================================
# 24. MechanismCombinationTests（机制组合场景）
# ============================================================================

class MechanismCombinationTests(unittest.TestCase):
    def test_ace_low_hearts_big_x_but_silenced(self):
        """A=1获得X=4但被沉默阻断。"""
        g = _combat_game(); _set_action(g)
        # 攻击：红桃A（选A=1, X=4），但被沉默
        g.players[0].front[0] = Troop([Card(Suit.HEARTS, Rank.ACE)])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 10
        # 防御：黑桃K(20>=1)，首张黑桃+黑桃总和20>=1 → 沉默
        g.players[1].front[0] = Troop([Card(Suit.SPADES, Rank.KING)])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        # A选1
        report = g.op_attack(0, AutoAgent(ace_high=False), AutoAgent())
        self.assertTrue(report.silenced)
        # 红桃A弃牌效果被沉默，手牌不变
        self.assertEqual(g.players[0].hand_size, before_hand)

    def test_rescue_with_spade_double(self):
        """急救打出的黑桃牌可以选择翻倍。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.FIVE)])  # 10
        # 防御：红桃2(2)→急救打出黑桃3(3*2=6)→总防2+6=8<10...
        # 需要更大：红桃4(4)→急救黑桃5(5*2=10)→4+10=14>=10
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.FOUR)])
        g.players[1].hand = [Card(Suit.SPADES, Rank.FIVE)] + g.players[1].hand
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        class RescueDoubleAgent:
            def decide(self, req):
                if req["type"] == "rescue_choice": return 0
                if req["type"] == "spade_double_choice": return True
                if req["type"] == "ace_choice": return True
                if req["type"] == "continue_defense": return False
                if req["type"] in ("hearts_draw","diamonds_draw"): return req.get("max",0)
                return None
        report = g.op_attack(0, AutoAgent(), RescueDoubleAgent())
        self.assertTrue(report.rescue_used)
        self.assertTrue(report.defense_held)
        # 确认黑桃5翻倍了(=10)
        rescue_evt = [e for e in report.defense_cards if e.source == "hand_rescue"]
        self.assertEqual(len(rescue_evt), 1)
        self.assertEqual(rescue_evt[0].value, 10)
        self.assertTrue(rescue_evt[0].spade_doubled)

    def test_partial_prepare_then_overflow_hits_back(self):
        """部分前移后，前排空但后排有牌，溢出打待战区。"""
        g = _combat_game(); _set_action(g)
        # P0 攻击
        g.players[0].front[0] = Troop([Card(Suit.CLUBS, Rank.TEN)])  # 20
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        # P1 作战区：红桃2(2)被击毁
        g.players[1].front[0] = Troop([Card(Suit.HEARTS, Rank.TWO)])
        # P1 待战区有牌（模拟部分前移的场景）
        g.players[1].back[0] = Troop([
            Card(Suit.SPADES, Rank.KING),  # 10
            Card(Suit.SPADES, Rank.KING),  # 10 → 20>=18
        ])
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 5
        report = g.op_attack(0, AutoAgent(target="back"), AutoAgent(spade_double=False))
        self.assertTrue(report.troop_destroyed)
        self.assertEqual(report.overflow, 18)
        self.assertIsNotNone(report.overflow_report)
        self.assertEqual(report.overflow_report.target_type, "back")
        self.assertTrue(report.overflow_report.defense_held)

    def test_clubs_attack_with_hearts_second_no_discard(self):
        """梅花+红桃攻击：第一张梅花→红桃不触发弃牌（第一张花色规则）。"""
        g = _combat_game(); _set_action(g)
        g.players[0].front[0] = Troop([
            Card(Suit.CLUBS, Rank.THREE),   # 第一张梅花 6(翻倍)
            Card(Suit.HEARTS, Rank.FIVE),   # 红桃，不触发弃牌
        ])
        g.players[0].deck = [Card(Suit.SPADES, Rank.TWO)] * 8
        g.players[1].front[0] = Troop([Card(Suit.CLUBS, Rank.KING)])  # 10>=11? 6+5=11
        g.players[1].deck = [Card(Suit.SPADES, Rank.TWO)] * 3
        before_hand = g.players[0].hand_size
        report = g.op_attack(0, AutoAgent(), AutoAgent(spade_double=False))
        self.assertTrue(report.clubs_doubled)
        # 第一张是梅花→红桃弃牌不执行
        self.assertEqual(g.players[0].hand_size, before_hand)


if __name__ == "__main__":
    unittest.main()

