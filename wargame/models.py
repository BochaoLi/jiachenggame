"""卡牌与玩家状态模型 — 按原版 Poker Battle 1.8 规则。

区域：手牌、牌库（大本营）、墓地、作战区(×3)、待战区(×3)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# =========================================================================
# 花色 / 点数
# =========================================================================

class Suit(Enum):
    SPADES = "♠"
    HEARTS = "♥"
    CLUBS = "♣"
    DIAMONDS = "♦"

    @property
    def cn(self) -> str:
        return {Suit.SPADES: "黑桃", Suit.HEARTS: "红桃",
                Suit.CLUBS: "梅花", Suit.DIAMONDS: "方片"}[self]


class Rank(Enum):
    ACE = "A"; TWO = "2"; THREE = "3"; FOUR = "4"; FIVE = "5"
    SIX = "6"; SEVEN = "7"; EIGHT = "8"; NINE = "9"; TEN = "10"
    JACK = "J"; QUEEN = "Q"; KING = "K"


@dataclass(frozen=True)
class Card:
    suit: Suit
    rank: Rank

    def __str__(self) -> str:
        return f"{self.suit.value}{self.rank.value}"

    @property
    def is_ace(self) -> bool:
        return self.rank == Rank.ACE

    # --- 点数计算 ---

    def base_value(self, ace_high: bool = False) -> int:
        """常规点数：A=1/11，JQK=10，其余面值。"""
        if self.is_ace:
            return 11 if ace_high else 1
        if self.rank in (Rank.JACK, Rank.QUEEN, Rank.KING, Rank.TEN):
            return 10
        return int(self.rank.value)

    def hq_penalty_value(self) -> int:
        """大本营惩罚点数：A=1，JQK10=5，其余面值。"""
        if self.is_ace:
            return 1
        if self.rank in (Rank.JACK, Rank.QUEEN, Rank.KING, Rank.TEN):
            return 5
        return int(self.rank.value)

    # --- 弃牌效果 X 值 ---

    def discard_x(self, ace_high: bool = False) -> int:
        """红桃/方片弃牌效果的 X 值。"""
        if self.is_ace:
            return 1 if ace_high else 4
        v = self.base_value()
        if 2 <= v <= 5:
            return 3
        if 6 <= v <= 9:
            return 2
        return 1  # 10JQK


def build_full_deck() -> List[Card]:
    return [Card(s, r) for s in Suit for r in Rank]


# =========================================================================
# 部队（一叠牌）
# =========================================================================

@dataclass
class Troop:
    """一支部队：最多 5 张牌，cards[0] = 顶部。"""
    cards: List[Card] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.cards)

    @property
    def empty(self) -> bool:
        return len(self.cards) == 0

    def peek_top(self, n: int = 1) -> List[Card]:
        return self.cards[:min(n, len(self.cards))]

    def pop_top(self, n: int = 1) -> List[Card]:
        n = min(n, len(self.cards))
        popped = self.cards[:n]
        self.cards = self.cards[n:]
        return popped

    def add_bottom(self, cards: List[Card]) -> None:
        self.cards.extend(cards)

    def add_top(self, cards: List[Card]) -> None:
        self.cards = cards + self.cards


MAX_TROOP_SIZE = 5
NUM_ZONES = 3


# =========================================================================
# 玩家状态
# =========================================================================

@dataclass
class PlayerState:
    name: str
    hand: List[Card] = field(default_factory=list)
    deck: List[Card] = field(default_factory=list)       # 牌库，deck[0]=顶
    # 作战区 & 待战区各 3 格
    front: List[Troop] = field(default_factory=lambda: [Troop() for _ in range(NUM_ZONES)])
    back: List[Troop] = field(default_factory=lambda: [Troop() for _ in range(NUM_ZONES)])

    @property
    def deck_size(self) -> int:
        return len(self.deck)

    @property
    def hand_size(self) -> int:
        return len(self.hand)

    def is_defeated(self) -> bool:
        return len(self.deck) == 0

    def draw_from_deck(self, count: int) -> List[Card]:
        count = min(count, len(self.deck))
        drawn = self.deck[:count]
        self.deck = self.deck[count:]
        return drawn

    def deck_to_hand(self, count: int) -> List[Card]:
        drawn = self.draw_from_deck(count)
        self.hand.extend(drawn)
        return drawn

    def front_sizes(self) -> List[int]:
        return [t.size for t in self.front]

    def back_sizes(self) -> List[int]:
        return [t.size for t in self.back]
