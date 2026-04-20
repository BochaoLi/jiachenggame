"""Poker Battle 1.8 游戏引擎。

完整实现原版规则书中所有基础规则与可选特殊规则。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    Card, Suit, Rank, Troop, PlayerState,
    build_full_deck, MAX_TROOP_SIZE, NUM_ZONES,
)

# =========================================================================
# 异常 / 枚举
# =========================================================================

class GameOverError(Exception):
    def __init__(self, winner: int, loser: int, reason: str = "", payload=None):
        super().__init__(reason)
        self.winner_idx = winner
        self.loser_idx = loser
        self.reason = reason
        self.payload = payload


class IllegalActionError(ValueError):
    pass


class Phase(Enum):
    SETUP = "setup"
    MULLIGAN = "mulligan"          # 开局换牌
    INITIAL_DEPLOY = "initial_deploy"  # 初始部署
    PREPARE = "prepare"            # 准备阶段（待战→作战）
    ACTION = "action"              # 行动阶段
    DEPLOY = "deploy"              # 部署阶段
    GAME_OVER = "game_over"


class ActionKind(Enum):
    RECRUIT = "recruit"            # 征兵：墓地→牌库
    TRAIN = "train"                # 训练：牌库→手牌
    REORGANIZE = "reorganize"      # 重编：合并相邻作战区
    BALANCE = "balance"            # 制衡：手牌↔牌库 / 手牌↔墓地
    ATTACK = "attack"              # 进攻


# =========================================================================
# 特殊规则开关
# =========================================================================

@dataclass
class SpecialRules:
    four_element_defense: bool = False   # 四象防御
    consecutive_cards: bool = False      # 连张


# =========================================================================
# 攻击结算报告
# =========================================================================

@dataclass
class FlipEvent:
    player: int
    card: Card
    role: str          # "attack" / "defense"
    value: int         # 实际计算点数
    ace_high: Optional[bool] = None
    source: str = "front"  # "front" / "back" / "hq" / "hand_rescue"
    spade_doubled: bool = False

@dataclass
class DiscardEffect:
    player: int
    card: Card
    suit: Suit
    x_value: int = 0
    executed: bool = False  # 是否实际执行了效果
    description: str = ""

@dataclass
class AttackReport:
    attacker_zone: int
    target_type: str = "front"     # "front" / "back" / "hq"
    target_zone: int = -1

    attack_cards: List[FlipEvent] = field(default_factory=list)
    total_attack: int = 0
    clubs_doubled: bool = False    # 梅花翻倍是否生效

    defense_cards: List[FlipEvent] = field(default_factory=list)
    total_defense: int = 0

    troop_destroyed: bool = False  # 被击毁
    defense_held: bool = False     # 防御成功
    silenced: bool = False         # 沉默效果触发
    rescue_used: bool = False      # 急救效果使用
    rescue_card: Optional[Card] = None

    overflow: int = 0              # 溢出伤害
    overflow_report: Optional["AttackReport"] = None  # 溢出攻击的后续报告

    discard_effects: List[DiscardEffect] = field(default_factory=list)

    four_element_triggered: bool = False  # 四象防御触发


# =========================================================================
# Game
# =========================================================================

class Game:
    def __init__(
        self,
        player_names: Tuple[str, str] = ("玩家1", "玩家2"),
        seed: Optional[int] = None,
        special_rules: Optional[SpecialRules] = None,
    ):
        self.players = [PlayerState(player_names[0]), PlayerState(player_names[1])]
        self.graveyard: List[Card] = []  # 墓地
        self.rng = random.Random(seed)
        self.phase = Phase.SETUP
        self.current = 0
        self.turn = 0
        self.winner: Optional[int] = None
        self.actions_used: List[ActionKind] = []
        self.action_points = 0
        self.special = special_rules or SpecialRules()
        self.first_player = 0
        self.mulligan_done = [False, False]
        self.initial_deploy_done = [False, False]
        self._first_action_turn = True  # 先手第一个行动回合仅1点

    # ----- 查询 -----
    @property
    def me(self) -> PlayerState:
        return self.players[self.current]

    @property
    def opp(self) -> PlayerState:
        return self.players[1 - self.current]

    def opponent_of(self, idx: int) -> int:
        return 1 - idx

    def available_actions(self) -> List[ActionKind]:
        if self.phase != Phase.ACTION or self.action_points <= 0:
            return []
        return [a for a in ActionKind if a not in self.actions_used]

    # ----- 墓地操作 -----
    def _to_graveyard(self, cards: List[Card]) -> None:
        self.graveyard.extend(cards)
        self.rng.shuffle(self.graveyard)

    def _draw_from_graveyard(self, count: int) -> List[Card]:
        count = min(count, len(self.graveyard))
        drawn = self.graveyard[:count]
        self.graveyard = self.graveyard[count:]
        return drawn

    # ----- 设置 -----
    def setup(self, first_player: int = 0) -> None:
        if self.phase != Phase.SETUP:
            raise IllegalActionError("游戏已开始")
        deck = build_full_deck()
        self.rng.shuffle(deck)
        idx = 0
        for p in self.players:
            p.hand = deck[idx:idx + 7]; idx += 7
            p.deck = deck[idx:idx + 13]; idx += 13
            for z in range(NUM_ZONES):
                p.front[z] = Troop()
                p.back[z] = Troop()
        self.graveyard = deck[idx:]
        self.rng.shuffle(self.graveyard)
        self.first_player = first_player
        self.current = first_player
        self.turn = 0
        self.phase = Phase.MULLIGAN

    # ----- 换牌阶段 -----
    def mulligan(self, card_indices: List[int]) -> List[Card]:
        """选择至多 3 张手牌与墓地交换。card_indices 是手牌中的索引。"""
        if self.phase != Phase.MULLIGAN:
            raise IllegalActionError("当前不是换牌阶段")
        if self.mulligan_done[self.current]:
            raise IllegalActionError("你已完成换牌")
        if len(card_indices) > 3:
            raise IllegalActionError("至多换 3 张")
        if len(set(card_indices)) != len(card_indices):
            raise IllegalActionError("索引不能重复")
        me = self.me
        for i in card_indices:
            if i < 0 or i >= me.hand_size:
                raise IllegalActionError(f"索引越界: {i}")

        # 从墓地抽等量牌
        drawn = self._draw_from_graveyard(len(card_indices))
        # 把选中的手牌放入墓地
        discarded = [me.hand[i] for i in sorted(card_indices, reverse=True)]
        for i in sorted(card_indices, reverse=True):
            me.hand.pop(i)
        self._to_graveyard(discarded)
        me.hand.extend(drawn)

        self.mulligan_done[self.current] = True
        # 切换到下一个玩家，或进入初始部署
        if not all(self.mulligan_done):
            self.current = 1 - self.current
        else:
            self.current = self.first_player
            self.phase = Phase.INITIAL_DEPLOY
        return drawn

    # ----- 初始部署阶段 -----
    def initial_deploy(self, placements: Dict[int, List[Card]]) -> None:
        """初始部署：手牌→待战区。"""
        if self.phase != Phase.INITIAL_DEPLOY:
            raise IllegalActionError("当前不是初始部署阶段")
        self._do_deploy(placements)
        self.initial_deploy_done[self.current] = True
        if not all(self.initial_deploy_done):
            self.current = 1 - self.current
        else:
            self.current = self.first_player
            self.turn = 1
            self._first_action_turn = True
            self._begin_turn()

    # ----- 回合流程 -----
    def _begin_turn(self) -> None:
        self.phase = Phase.PREPARE

    def do_prepare(self, moves: Dict[int, int]) -> None:
        """准备阶段：选择每个待战区有多少张牌前移到作战区。

        moves: {zone_idx: count}，count=0 表示不移，-1 表示全部移。
        不提供的 zone 默认全部前移。
        """
        if self.phase != Phase.PREPARE:
            raise IllegalActionError("当前不是准备阶段")
        me = self.me
        for z in range(NUM_ZONES):
            back_troop = me.back[z]
            if back_troop.empty:
                continue
            count = moves.get(z, -1)
            if count < 0:
                count = back_troop.size
            count = min(count, back_troop.size)
            if count == 0:
                continue
            moving = back_troop.pop_top(count)
            front_troop = me.front[z]
            if front_troop.size + len(moving) > MAX_TROOP_SIZE:
                raise IllegalActionError(
                    f"作战区{z}移入后超过{MAX_TROOP_SIZE}张"
                )
            front_troop.add_bottom(moving)

        self.actions_used = []
        if self._first_action_turn:
            self.action_points = 1
            self._first_action_turn = False
        else:
            self.action_points = 2
        self.phase = Phase.ACTION

    # ----- 行动：征兵 -----
    def act_recruit(self) -> List[Card]:
        """征兵：墓地→牌库。"""
        self._check_action(ActionKind.RECRUIT)
        drawn = self._draw_from_graveyard(2)
        self.me.deck.extend(drawn)
        self._record_action(ActionKind.RECRUIT)
        return drawn

    # ----- 行动：训练 -----
    def act_train(self) -> List[Card]:
        """训练：牌库→手牌。"""
        self._check_action(ActionKind.TRAIN)
        drawn = self.me.deck_to_hand(2)
        self._record_action(ActionKind.TRAIN)
        self._check_loss()
        return drawn

    # ----- 行动：重编 -----
    def act_reorganize(
        self, zone_a: int, zone_b: int,
        new_a: List[Card], new_b: List[Card],
    ) -> None:
        """重编：合并相邻作战区部队重新分配。"""
        self._check_action(ActionKind.REORGANIZE)
        if abs(zone_a - zone_b) != 1:
            raise IllegalActionError("重编只能选相邻作战区")
        me = self.me
        old = me.front[zone_a].cards + me.front[zone_b].cards
        combined = sorted([(c.suit.name, c.rank.name) for c in old])
        proposed = sorted([(c.suit.name, c.rank.name) for c in new_a + new_b])
        if combined != proposed:
            raise IllegalActionError("重编后的牌必须与原两阵地完全一致")
        if len(new_a) > MAX_TROOP_SIZE or len(new_b) > MAX_TROOP_SIZE:
            raise IllegalActionError(f"单个部队不能超过 {MAX_TROOP_SIZE}")
        me.front[zone_a] = Troop(list(new_a))
        me.front[zone_b] = Troop(list(new_b))
        self._record_action(ActionKind.REORGANIZE)

    # ----- 行动：制衡 -----
    def act_balance_deck(self, swaps: List[Tuple[int, int]]) -> None:
        """制衡（模式 A）：手牌↔牌库交换，至多 4 次。swaps 为 [(hand_idx, deck_idx), ...]。"""
        self._check_action(ActionKind.BALANCE)
        if len(swaps) > 4:
            raise IllegalActionError("手牌↔牌库至多交换 4 次")
        me = self.me
        for hi, di in swaps:
            if hi < 0 or hi >= me.hand_size or di < 0 or di >= me.deck_size:
                raise IllegalActionError(f"索引越界: hand={hi}, deck={di}")
            me.hand[hi], me.deck[di] = me.deck[di], me.hand[hi]
        self._record_action(ActionKind.BALANCE)

    def act_balance_graveyard(self, hand_indices: List[int]) -> List[Card]:
        """制衡（模式 B）：至多 2 张手牌↔墓地交换，至多 1 次。"""
        self._check_action(ActionKind.BALANCE)
        if len(hand_indices) > 2:
            raise IllegalActionError("手牌↔墓地至多 2 张")
        me = self.me
        for i in hand_indices:
            if i < 0 or i >= me.hand_size:
                raise IllegalActionError(f"索引越界: {i}")
        drawn = self._draw_from_graveyard(len(hand_indices))
        discarded = [me.hand[i] for i in sorted(hand_indices, reverse=True)]
        for i in sorted(hand_indices, reverse=True):
            me.hand.pop(i)
        self._to_graveyard(discarded)
        me.hand.extend(drawn)
        self._record_action(ActionKind.BALANCE)
        return drawn

    # ----- 行动：进攻（生成器驱动） -----
    def attack_steps(self, attacker_zone: int):
        """进攻的可中断生成器。

        yields 决策请求，调用方 send 答案。
        最终 return AttackReport，或在败北时 raise GameOverError。
        """
        self._check_action(ActionKind.ATTACK)
        me = self.me
        opp = self.opp
        cur = self.current
        opp_idx = 1 - cur

        front_troop = me.front[attacker_zone]
        if front_troop.empty:
            raise IllegalActionError("该作战区没有部队")

        n_flip = min(2, front_troop.size)
        atk_cards_raw = front_troop.pop_top(n_flip)
        self._record_action(ActionKind.ATTACK)

        # 确定攻击目标
        opp_front = opp.front[attacker_zone]
        if not opp_front.empty:
            target_type = "front"
        elif not opp.back[attacker_zone].empty:
            target_choice = (yield {
                "type": "attack_target_choice",
                "by": cur,
                "zone": attacker_zone,
                "options": ["back", "hq"],
            })
            target_type = str(target_choice) if target_choice in ("back", "hq") else "back"
        else:
            target_type = "hq"

        report = AttackReport(
            attacker_zone=attacker_zone,
            target_type=target_type,
            target_zone=attacker_zone,
        )

        # --- 攻击牌结算：以第一张牌花色为准 ---
        first_suit = atk_cards_raw[0].suit
        clubs_active = (first_suit == Suit.CLUBS)
        report.clubs_doubled = clubs_active

        total_atk = 0
        for i, c in enumerate(atk_cards_raw):
            ace_high = False
            if c.is_ace:
                ace_high = bool((yield {
                    "type": "ace_choice", "by": cur, "card": c, "context": "attack",
                }))
            val = c.base_value(ace_high)
            # 梅花翻倍：第一张是梅花时，所有梅花牌翻倍
            if clubs_active and c.suit == Suit.CLUBS:
                val *= 2
            report.attack_cards.append(FlipEvent(
                player=cur, card=c, role="attack", value=val,
                ace_high=ace_high if c.is_ace else None,
            ))
            total_atk += val
        report.total_attack = total_atk

        # --- 防御结算 ---
        def_report = yield from self._resolve_defense(
            report, target_type, attacker_zone, opp_idx, cur
        )

        # --- 溢出攻击 ---
        if report.troop_destroyed and report.overflow > 0:
            overflow_target = None
            if target_type == "front":
                if not opp.back[attacker_zone].empty:
                    overflow_target = "back"
                else:
                    overflow_target = "hq"
            elif target_type == "back":
                overflow_target = "hq"

            if overflow_target:
                overflow_rpt = AttackReport(
                    attacker_zone=attacker_zone,
                    target_type=overflow_target,
                    target_zone=attacker_zone,
                    total_attack=report.overflow,
                )
                # 溢出攻击不再有攻击牌（是残余伤害）
                yield from self._resolve_defense(
                    overflow_rpt, overflow_target, attacker_zone, opp_idx, cur,
                    is_overflow=True,
                )
                report.overflow_report = overflow_rpt

        # --- 弃牌效果 ---
        all_discarded: List[Card] = []
        all_discarded.extend(c.card for c in report.attack_cards)
        all_discarded.extend(c.card for c in report.defense_cards)
        if report.overflow_report:
            all_discarded.extend(c.card for c in report.overflow_report.defense_cards)

        if not report.troop_destroyed or target_type == "front":
            # 击毁的部队跳过弃牌效果；但攻击方的弃牌效果仍执行
            yield from self._resolve_discard_effects(
                report, atk_cards_raw, cur, opp_idx
            )

        self._to_graveyard(all_discarded)
        self._check_loss()
        return report

    def _resolve_defense(
        self, report: AttackReport, target_type: str, zone: int,
        def_idx: int, atk_idx: int, is_overflow: bool = False,
    ):
        """防御结算子流程。"""
        defender = self.players[def_idx]
        remaining = report.total_attack

        if target_type == "hq":
            # 大本营防守惩罚
            while remaining > 0:
                if not defender.deck:
                    report.troop_destroyed = True
                    self.winner = atk_idx
                    self.phase = Phase.GAME_OVER
                    raise GameOverError(atk_idx, def_idx, "大本营被击毁", report)
                c = defender.deck.pop(0)
                val = c.hq_penalty_value()
                report.defense_cards.append(FlipEvent(
                    player=def_idx, card=c, role="defense", value=val, source="hq",
                ))
                remaining -= val
                report.total_defense += val
            report.defense_held = True
            return

        # 作战区 / 待战区防御
        if target_type == "front":
            troop = defender.front[zone]
        else:
            troop = defender.back[zone]

        first_defense_suit: Optional[Suit] = None
        defense_suits_seen: set = set()
        rescue_used = False

        while remaining > 0:
            if troop.empty:
                report.troop_destroyed = True
                report.overflow = remaining
                break

            c = troop.pop_top(1)[0]
            from_source = target_type

            # A 的选择（阵地牌可选）
            ace_high = False
            if c.is_ace:
                ace_high = bool((yield {
                    "type": "ace_choice", "by": def_idx, "card": c, "context": "defense",
                }))

            # 点数计算
            val = c.base_value(ace_high)
            spade_doubled = False

            # 黑桃翻倍（可选）——溢出攻击时待战区黑桃不执行沉默但仍可翻倍
            if c.suit == Suit.SPADES:
                if not is_overflow or target_type != "back":
                    choose_double = (yield {
                        "type": "spade_double_choice", "by": def_idx, "card": c,
                        "current_val": val, "doubled_val": val * 2,
                    })
                    if choose_double:
                        val *= 2
                        spade_doubled = True

            if first_defense_suit is None:
                first_defense_suit = c.suit
            defense_suits_seen.add(c.suit)

            evt = FlipEvent(
                player=def_idx, card=c, role="defense", value=val,
                ace_high=ace_high if c.is_ace else None,
                source=from_source, spade_doubled=spade_doubled,
            )
            report.defense_cards.append(evt)
            report.total_defense += val
            remaining -= val

            # 红桃急救：防守时翻出红桃可从手牌打出 1 张加入防御（每轮限一次）
            if c.suit == Suit.HEARTS and not rescue_used and defender.hand:
                use_rescue = (yield {
                    "type": "rescue_choice", "by": def_idx,
                    "trigger_card": c,
                    "hand": [str(hc) for hc in defender.hand],
                })
                if use_rescue is not None and use_rescue is not False:
                    try:
                        rescue_idx = int(use_rescue)
                        if 0 <= rescue_idx < len(defender.hand):
                            rc = defender.hand.pop(rescue_idx)
                            # 急救牌直接计入防御
                            r_ace = False
                            if rc.is_ace:
                                r_ace = bool((yield {
                                    "type": "ace_choice", "by": def_idx, "card": rc,
                                    "context": "defense_rescue",
                                }))
                            rv = rc.base_value(r_ace)
                            if rc.suit == Suit.SPADES:
                                r_double = (yield {
                                    "type": "spade_double_choice", "by": def_idx, "card": rc,
                                    "current_val": rv, "doubled_val": rv * 2,
                                })
                                if r_double:
                                    rv *= 2
                            r_evt = FlipEvent(
                                player=def_idx, card=rc, role="defense", value=rv,
                                ace_high=r_ace if rc.is_ace else None,
                                source="hand_rescue",
                                spade_doubled=(rc.suit == Suit.SPADES and rv == rc.base_value(r_ace) * 2),
                            )
                            report.defense_cards.append(r_evt)
                            report.total_defense += rv
                            remaining -= rv
                            report.rescue_used = True
                            report.rescue_card = rc
                            rescue_used = True
                            # 触发急救的红桃牌不触发弃牌效果（标记）
                            evt.source = "rescue_trigger"
                    except (ValueError, IndexError):
                        pass

            # 四象防御特殊规则
            if (self.special.four_element_defense
                    and len(defense_suits_seen) >= 4
                    and remaining > 0):
                report.four_element_triggered = True
                remaining = 0  # 立即防御成功

            # 防御方可选择继续翻牌
            if remaining <= 0 and not troop.empty:
                cont = (yield {
                    "type": "continue_defense", "by": def_idx,
                    "current_total": report.total_defense,
                    "attack_total": report.total_attack,
                    "remaining_in_troop": troop.size,
                })
                if cont:
                    remaining = 1  # 强制继续循环

        if not report.troop_destroyed:
            report.defense_held = True

        # 沉默判定：首张牌是黑桃 + 黑桃点数之和 ≥ 攻击点数
        if first_defense_suit == Suit.SPADES and not is_overflow:
            spade_sum = sum(
                e.value for e in report.defense_cards
                if e.card.suit == Suit.SPADES and e.source != "hand_rescue"
            )
            if spade_sum >= report.total_attack:
                report.silenced = True

    def _resolve_discard_effects(
        self, report: AttackReport, atk_cards: List[Card],
        atk_idx: int, def_idx: int,
    ):
        """弃牌效果结算：先攻方再防方。"""
        # 攻击方弃牌效果
        if not report.silenced:
            atk_first_suit = atk_cards[0].suit if atk_cards else None
            yield from self._do_discard_effects_for(
                atk_idx, atk_cards, atk_first_suit, report, "attack",
                zone=report.attacker_zone,
            )

        # 防御方弃牌效果（被击毁则跳过）
        if not report.troop_destroyed:
            def_cards = [e.card for e in report.defense_cards
                         if e.source not in ("hq", "rescue_trigger")]
            def_first_suit = report.defense_cards[0].card.suit if report.defense_cards else None

            # 四象防御时：所有红桃方片都结算（不受"第一张牌花色为准"限制）
            if report.four_element_triggered:
                def_first_suit = None  # 取消限制

            yield from self._do_discard_effects_for(
                def_idx, def_cards, def_first_suit, report, "defense",
                zone=report.target_zone,
                four_element=report.four_element_triggered,
            )

    def _do_discard_effects_for(
        self, player_idx: int, cards: List[Card],
        first_suit: Optional[Suit], report: AttackReport,
        role: str, zone: int = 0, four_element: bool = False,
    ):
        """对一方的弃牌执行花色效果。"""
        # 计算总 X（加和，上限 6）
        hearts_x = 0
        diamonds_x = 0
        for c in cards:
            ace_high_for_x = any(
                e.ace_high for e in (report.attack_cards if role == "attack" else report.defense_cards)
                if e.card == c and e.ace_high is not None
            )
            x = c.discard_x(ace_high_for_x)
            if c.suit == Suit.HEARTS:
                if four_element or first_suit is None or first_suit == Suit.HEARTS:
                    hearts_x += x
            elif c.suit == Suit.DIAMONDS:
                if four_element or first_suit is None or first_suit == Suit.DIAMONDS:
                    diamonds_x += x

        hearts_x = min(hearts_x, 6)
        diamonds_x = min(diamonds_x, 6)
        p = self.players[player_idx]

        # 红桃效果：牌库→手牌 X 张，然后可从手牌补 1 张到部队底端
        if hearts_x > 0:
            actual = min(hearts_x, p.deck_size)
            if actual > 0:
                choose = (yield {
                    "type": "hearts_draw", "by": player_idx,
                    "max": actual, "role": role,
                })
                n = max(0, min(_safe_int(choose), actual))
                drawn = p.deck_to_hand(n)
                report.discard_effects.append(DiscardEffect(
                    player_idx, cards[0], Suit.HEARTS, hearts_x, True,
                    f"抽{len(drawn)}张到手牌",
                ))
                # 补充部队
                self._check_loss()

        # 方片效果：墓地→牌库 X 张，并可调整当前部队排序
        if diamonds_x > 0:
            actual = min(diamonds_x, len(self.graveyard))
            if actual > 0:
                choose = (yield {
                    "type": "diamonds_draw", "by": player_idx,
                    "max": actual, "role": role,
                })
                n = max(0, min(_safe_int(choose), actual))
                drawn = self._draw_from_graveyard(n)
                p.deck.extend(drawn)
                report.discard_effects.append(DiscardEffect(
                    player_idx, cards[0], Suit.DIAMONDS, diamonds_x, True,
                    f"从墓地抽{len(drawn)}张到牌库",
                ))

    # ----- 结束行动阶段 -----
    def end_action_phase(self) -> None:
        if self.phase != Phase.ACTION:
            raise IllegalActionError("当前不是行动阶段")
        self.phase = Phase.DEPLOY

    # ----- 部署阶段 -----
    def deploy(self, placements: Dict[int, List[Card]]) -> None:
        if self.phase not in (Phase.DEPLOY, Phase.INITIAL_DEPLOY):
            raise IllegalActionError("当前不是部署阶段")
        self._do_deploy(placements)

    def _do_deploy(self, placements: Dict[int, List[Card]]) -> None:
        me = self.me
        hand_copy = list(me.hand)
        for zone, cards in placements.items():
            if not (0 <= zone < NUM_ZONES):
                raise IllegalActionError(f"区域编号无效: {zone}")
            back = me.back[zone]
            if back.size + len(cards) > MAX_TROOP_SIZE:
                raise IllegalActionError(
                    f"待战区{zone}部署后超过{MAX_TROOP_SIZE}张"
                )
            for c in cards:
                try:
                    hand_copy.remove(c)
                except ValueError:
                    raise IllegalActionError(f"手牌中没有 {c}")
            back.add_bottom(cards)
        me.hand = hand_copy

    def end_turn(self) -> None:
        if self.phase not in (Phase.ACTION, Phase.DEPLOY):
            raise IllegalActionError("当前不能结束回合")
        self.current = 1 - self.current
        self.turn += 1
        self._begin_turn()

    # ----- 辅助 -----
    def _check_action(self, kind: ActionKind) -> None:
        if self.phase != Phase.ACTION:
            raise IllegalActionError("当前不是行动阶段")
        if self.action_points <= 0:
            raise IllegalActionError("行动力已用完")
        if kind in self.actions_used:
            raise IllegalActionError(f"{kind.value} 本回合已使用")

    def _record_action(self, kind: ActionKind) -> None:
        self.actions_used.append(kind)
        self.action_points -= 1

    def _check_loss(self) -> None:
        if self.phase == Phase.GAME_OVER:
            return
        for i in range(2):
            if self.players[i].is_defeated():
                w = 1 - i
                self.winner = w
                self.phase = Phase.GAME_OVER
                raise GameOverError(w, i, "牌库被掏空")

    # ----- 同步版攻击（用于 CLI / AI） -----
    def op_attack(self, zone: int, agent_a, agent_b) -> AttackReport:
        agents = [agent_a, agent_b]
        gen = self.attack_steps(zone)
        answer: Any = None
        try:
            while True:
                req = next(gen) if answer is None else gen.send(answer)
                by = req.get("by", self.current)
                answer = agents[by].decide(req)
        except StopIteration as e:
            return e.value


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
