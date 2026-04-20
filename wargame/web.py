"""Poker Battle 1.8 网页服务器。"""
from __future__ import annotations
import json, os, sys, threading, traceback, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .engine import (
    AttackReport, FlipEvent, Game, GameOverError, Phase,
    IllegalActionError, ActionKind, SpecialRules,
)
from .models import Card, Suit, Rank, Troop, NUM_ZONES, MAX_TROOP_SIZE
from .ai import ChampionAI

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

class _NoopAgent:
    def decide(self, req): raise RuntimeError("Web 模式不走同步 agent")

# ---------- Session ----------
class Session:
    def __init__(self):
        self.game: Optional[Game] = None
        self.log: List[Dict] = []
        self.lock = threading.Lock()
        self.attack_gen: Optional[Generator] = None
        self.pending: Optional[Dict] = None
        self.ai: Optional[ChampionAI] = None
        self.ai_idx: Optional[int] = None  # None=PvP, 0/1=AI player

    def reset(self, names, seed, first, special, ai_player=None):
        self.game = Game(names, seed=seed, special_rules=special)
        self.game.setup(first_player=first)
        self.log = []
        self.attack_gen = None
        self.pending = None
        self.ai_idx = ai_player
        if ai_player is not None:
            self.ai = ChampionAI(ai_player, self.game)
        else:
            self.ai = None
        _log(f"新对局：{names[0]} vs {names[1]}（{names[first]} 先手）")
        if ai_player is not None:
            _log(f"AI 对手：{names[ai_player]}（冠军策略 Elo 1960）")
        sr = []
        if special.four_element_defense: sr.append("四象防御")
        if special.consecutive_cards: sr.append("连张")
        if sr: _log(f"特殊规则：{', '.join(sr)}")

SESSION = Session()

def _log(text):
    SESSION.log.append({"text": text})
    if len(SESSION.log) > 300: SESSION.log = SESSION.log[-300:]

# ---------- 序列化 ----------
def _card(c: Card) -> Dict:
    return {"suit": c.suit.name, "suit_symbol": c.suit.value, "suit_cn": c.suit.cn,
            "rank": c.rank.name, "rank_label": c.rank.value, "id": f"{c.suit.name}_{c.rank.name}"}

def _cards(cs): return [_card(c) for c in cs]

def _troop(t: Troop, visible: bool):
    return {"size": t.size, "cards": _cards(t.cards) if visible else None}

def serialize_state(viewer: int) -> Dict:
    g = SESSION.game
    if not g: return {"started": False}
    players = []
    for i, p in enumerate(g.players):
        me = i == viewer
        players.append({
            "idx": i, "name": p.name, "is_me": me,
            "is_current": i == g.current,
            "hand_count": p.hand_size, "deck_count": p.deck_size,
            "hand": _cards(p.hand) if me else None,
            "front": [_troop(p.front[z], me) for z in range(NUM_ZONES)],
            "back": [_troop(p.back[z], me) for z in range(NUM_ZONES)],
        })
    return {
        "started": True, "phase": g.phase.value,
        "current": g.current, "viewer": viewer,
        "turn": g.turn, "action_points": g.action_points,
        "actions_used": [a.value for a in g.actions_used],
        "available_actions": [a.value for a in g.available_actions()],
        "graveyard_count": len(g.graveyard),
        "winner": g.winner,
        "special": {"four_element_defense": g.special.four_element_defense,
                    "consecutive_cards": g.special.consecutive_cards},
        "mulligan_done": g.mulligan_done[:],
        "initial_deploy_done": g.initial_deploy_done[:],
        "max_troop": MAX_TROOP_SIZE, "num_zones": NUM_ZONES,
        "log": SESSION.log[-80:],
        "pending_request": _serialize_pending(),
        "players": players,
        "ai_player_idx": SESSION.ai_idx,
        "is_ai_turn": (SESSION.ai_idx is not None and g.current == SESSION.ai_idx
                       and g.phase not in (Phase.GAME_OVER, Phase.SETUP)),
    }

def _serialize_pending():
    r = SESSION.pending
    if not r: return None
    out = {k: v for k, v in r.items() if k != "card" and k != "trigger_card"}
    if "card" in r: out["card"] = _card(r["card"])
    if "trigger_card" in r: out["trigger_card"] = _card(r["trigger_card"])
    return out

def _card_from_id(cid: str) -> Card:
    s, r = cid.split("_")
    return Card(Suit[s], Rank[r])

# ---------- 攻击生成器驱动 ----------
def _advance_attack(answer) -> Dict:
    g = SESSION.game
    gen = SESSION.attack_gen
    if not gen: return {"ok": False, "message": "没有进行中的攻击"}
    try:
        if answer is None:
            req = next(gen)
        else:
            req = gen.send(answer)
        # AI 自动应答循环
        while True:
            if SESSION.ai and req.get("by") == SESSION.ai_idx:
                ai_ans = SESSION.ai.decide(req)
                req = gen.send(ai_ans)
            else:
                SESSION.pending = req
                return {"ok": True, "pending": True, "request": _serialize_pending()}
    except StopIteration as e:
        SESSION.attack_gen = None; SESSION.pending = None
        report = e.value
        _log_attack(report)
        return {"ok": True, "pending": False, "attack_report": _serialize_report(report)}
    except GameOverError as e:
        SESSION.attack_gen = None; SESSION.pending = None
        if isinstance(e.payload, AttackReport): _log_attack(e.payload)
        _log(f"##### 游戏结束：{g.players[e.winner_idx].name} 获胜（{e.reason}）#####")
        return {"ok": True, "pending": False, "game_over": {"winner": e.winner_idx, "reason": e.reason}}

def _serialize_report(r: AttackReport) -> Dict:
    def ev(e: FlipEvent):
        return {"player": e.player, "card": _card(e.card), "role": e.role,
                "value": e.value, "ace_high": e.ace_high, "source": e.source,
                "spade_doubled": e.spade_doubled}
    out = {"attacker_zone": r.attacker_zone, "target_type": r.target_type,
           "attack_cards": [ev(e) for e in r.attack_cards],
           "total_attack": r.total_attack, "clubs_doubled": r.clubs_doubled,
           "defense_cards": [ev(e) for e in r.defense_cards],
           "total_defense": r.total_defense,
           "troop_destroyed": r.troop_destroyed, "defense_held": r.defense_held,
           "silenced": r.silenced, "rescue_used": r.rescue_used,
           "overflow": r.overflow, "four_element": r.four_element_triggered}
    if r.overflow_report: out["overflow_report"] = _serialize_report(r.overflow_report)
    return out

def _log_attack(r: AttackReport):
    g = SESSION.game
    atk = g.players[g.current].name; dfn = g.players[1-g.current].name
    _log(f"{atk} 从作战区{r.attacker_zone}攻击{dfn}的{r.target_type}")
    for e in r.attack_cards:
        _log(f"  攻击牌 {e.card.suit.cn}{e.card.rank.value} = {e.value}" +
             (" (梅花翻倍)" if r.clubs_doubled and e.card.suit == Suit.CLUBS else ""))
    _log(f"  总攻击 = {r.total_attack}")
    for e in r.defense_cards:
        tag = f"({e.source})" if e.source != "front" else ""
        _log(f"    防御 {e.card.suit.cn}{e.card.rank.value} = {e.value}{tag}" +
             (" ♠×2" if e.spade_doubled else ""))
    _log(f"    累计防御 = {r.total_defense}")
    if r.silenced: _log("    沉默！攻击方弃牌效果被无效化")
    if r.troop_destroyed: _log(f"    部队被击毁！溢出 {r.overflow}")
    elif r.defense_held: _log("    防御成功")
    if r.rescue_used: _log(f"    急救使用：{r.rescue_card}")
    if r.overflow_report: _log_attack(r.overflow_report)

# ---------- AI 回合 ----------
def _run_ai_turn() -> Dict:
    """执行 AI 的完整回合（一次性完成：准备+行动+部署+结束）。"""
    g = SESSION.game
    ai = SESSION.ai
    if not g or not ai: return {"ok": False, "message": "无 AI"}
    if g.current != SESSION.ai_idx: return {"ok": False, "message": "不是 AI 的回合"}
    if g.phase == Phase.GAME_OVER: return {"ok": True}

    ai_name = g.players[SESSION.ai_idx].name
    try:
        # 换牌阶段
        if g.phase == Phase.MULLIGAN:
            indices = ai.do_mulligan()
            g.mulligan(indices)
            _log(f"{ai_name} 换牌 {len(indices)} 张")
            return {"ok": True}

        # 初始部署
        if g.phase == Phase.INITIAL_DEPLOY:
            placements = ai.do_initial_deploy()
            try:
                g.initial_deploy(placements)
            except Exception:
                g.initial_deploy({})
            total = sum(len(v) for v in placements.values())
            _log(f"{ai_name} 初始部署 {total} 张")
            return {"ok": True}

        # 正常回合：一次性跑完 准备→行动→部署→结束回合
        # 准备阶段
        if g.phase == Phase.PREPARE:
            g.do_prepare({})
            _log(f"{ai_name} 准备阶段：全部前移")

        # 行动阶段
        if g.phase == Phase.ACTION:
            actions = ai.play_turn()
            for act in actions:
                if act["type"] == "train":
                    _log(f"{ai_name} 训练")
                elif act["type"] == "recruit":
                    _log(f"{ai_name} 征兵")
                elif act["type"] == "reorg":
                    _log(f"{ai_name} 重编")
                elif act["type"] == "attack":
                    zone = act["zone"]
                    _log(f"{ai_name} 发起攻击（作战区{zone}）")
                    try:
                        gen = g.attack_steps(zone)
                        ans = None
                        try:
                            while True:
                                req = next(gen) if ans is None else gen.send(ans)
                                ans = ai.decide(req)
                        except StopIteration as e:
                            report = e.value
                            _log_attack(report)
                            return {"ok": True, "attack_report": _serialize_report(report)}
                    except GameOverError as e:
                        if isinstance(e.payload, AttackReport): _log_attack(e.payload)
                        _log(f"##### 游戏结束：{g.players[e.winner_idx].name} 获胜 #####")
                        return {"ok": True, "game_over": {"winner": e.winner_idx, "reason": e.reason}}
                elif act["type"] == "deploy":
                    pass  # deploy 在 play_turn 内已执行

            # 如果还在 ACTION 阶段（没攻击），结束行动
            if g.phase == Phase.ACTION:
                g.end_action_phase()

        # 部署阶段
        if g.phase == Phase.DEPLOY:
            placements = ai._choose_deploy()
            try:
                g.deploy(placements)
            except Exception:
                g.deploy({})
            total = sum(len(v) for v in placements.values())
            _log(f"{ai_name} 部署 {total} 张")

        # 结束回合
        if g.phase in (Phase.ACTION, Phase.DEPLOY):
            g.end_turn()
            _log(f"{ai_name} 结束回合")

        return {"ok": True}
    except GameOverError as e:
        _log(f"##### 游戏结束：{g.players[e.winner_idx].name} 获胜 #####")
        return {"ok": True, "game_over": {"winner": e.winner_idx, "reason": e.reason}}
    except Exception as e:
        # 发生异常时强制结束 AI 回合避免死循环
        if g.phase == Phase.ACTION:
            try: g.end_action_phase()
            except: pass
        if g.phase == Phase.DEPLOY:
            try: g.deploy({})
            except: pass
            try: g.end_turn()
            except: pass
        _log(f"{ai_name} 回合异常: {e}")
        return {"ok": True}

# ---------- 动作执行 ----------
def _do_action(action, params) -> Dict:
    g = SESSION.game
    if not g: return {"ok": False, "message": "游戏未开始"}
    if SESSION.attack_gen and action != "attack_decision":
        return {"ok": False, "message": "攻击结算中"}
    try:
        name = g.players[g.current].name
        if action == "mulligan":
            indices = params.get("indices", [])
            drawn = g.mulligan(indices)
            _log(f"{name} 换牌 {len(indices)} 张")
            return {"ok": True}
        if action == "initial_deploy":
            plc = {int(k): [_card_from_id(c) for c in v] for k, v in params.get("placements", {}).items()}
            g.initial_deploy(plc)
            total = sum(len(v) for v in plc.values())
            _log(f"{name} 初始部署 {total} 张")
            return {"ok": True}
        if action == "prepare":
            moves = {int(k): int(v) for k, v in params.get("moves", {}).items()}
            g.do_prepare(moves)
            _log(f"{name} 准备阶段完成")
            return {"ok": True}
        if action == "recruit":
            drawn = g.act_recruit()
            _log(f"{name} 征兵：墓地→牌库 {len(drawn)} 张")
            return {"ok": True}
        if action == "train":
            drawn = g.act_train()
            _log(f"{name} 训练：牌库→手牌 {len(drawn)} 张")
            return {"ok": True}
        if action == "reorganize":
            za, zb = int(params["zone_a"]), int(params["zone_b"])
            na = [_card_from_id(c) for c in params["new_a"]]
            nb = [_card_from_id(c) for c in params["new_b"]]
            g.act_reorganize(za, zb, na, nb)
            _log(f"{name} 重编作战区{za}和{zb}")
            return {"ok": True}
        if action == "balance_deck":
            swaps = [(s[0], s[1]) for s in params.get("swaps", [])]
            g.act_balance_deck(swaps)
            _log(f"{name} 制衡（手牌↔牌库）{len(swaps)}次")
            return {"ok": True}
        if action == "balance_graveyard":
            indices = params.get("hand_indices", [])
            g.act_balance_graveyard(indices)
            _log(f"{name} 制衡（手牌↔墓地）{len(indices)}张")
            return {"ok": True}
        if action == "attack":
            zone = int(params["zone"])
            gen = g.attack_steps(zone)
            SESSION.attack_gen = gen; SESSION.pending = None
            _log(f"{name} 发起攻击（作战区{zone}）")
            return _advance_attack(None)
        if action == "end_action_phase":
            g.end_action_phase()
            _log(f"{name} 结束行动阶段")
            return {"ok": True}
        if action == "deploy":
            plc = {int(k): [_card_from_id(c) for c in v] for k, v in params.get("placements", {}).items()}
            g.deploy(plc)
            total = sum(len(v) for v in plc.values())
            _log(f"{name} 部署 {total} 张到待战区")
            return {"ok": True}
        if action == "end_turn":
            old = name; g.end_turn()
            _log(f"{old} 结束回合，轮到 {g.players[g.current].name}")
            return {"ok": True}
        return {"ok": False, "message": f"未知动作: {action}"}
    except GameOverError as e:
        _log(f"##### 游戏结束：{g.players[e.winner_idx].name} 获胜（{e.reason}）#####")
        return {"ok": True, "game_over": {"winner": e.winner_idx, "reason": e.reason}}
    except IllegalActionError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"参数错误: {e}"}

# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "PokerBattle/2.0"
    def log_message(self, fmt, *a): sys.stderr.write(f"[web] {fmt % a}\n")
    def _json(self, code, data):
        b = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type","application/json;charset=utf-8")
        self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store")
        self.end_headers(); self.wfile.write(b)
    def _file(self, fp):
        if not os.path.isfile(fp): self.send_error(404); return
        ct = {".html":"text/html;charset=utf-8",".js":"application/javascript;charset=utf-8",
              ".css":"text/css;charset=utf-8"}.get(os.path.splitext(fp)[1],"application/octet-stream")
        with open(fp,"rb") as f: d=f.read()
        self.send_response(200); self.send_header("Content-Type",ct)
        self.send_header("Content-Length",str(len(d))); self.send_header("Cache-Control","no-store")
        self.end_headers(); self.wfile.write(d)
    def _body(self):
        n=int(self.headers.get("Content-Length","0"))
        return json.loads(self.rfile.read(n)) if n else {}
    def do_GET(self):
        u=urlparse(self.path)
        if u.path in ("/","/index.html"): self._file(os.path.join(STATIC_DIR,"index.html")); return
        if u.path=="/api/state":
            v=int(parse_qs(u.query).get("viewer",["0"])[0])
            with SESSION.lock: self._json(200, serialize_state(v)); return
        if u.path.startswith("/static/"):
            self._file(os.path.join(STATIC_DIR, u.path[8:])); return
        self.send_error(404)
    def do_POST(self):
        u=urlparse(self.path)
        try: body=self._body()
        except: self._json(400,{"ok":False,"message":"bad json"}); return
        with SESSION.lock:
            if u.path=="/api/new_game":
                n1=(body.get("name1") or "玩家A").strip() or "玩家A"
                n2=(body.get("name2") or "玩家B").strip() or "玩家B"
                seed=body.get("seed"); seed=int(seed) if seed not in (None,"") else None
                first=int(body.get("first_player",0))
                sp = SpecialRules(
                    four_element_defense=bool(body.get("four_element",False)),
                    consecutive_cards=bool(body.get("consecutive",False)),
                )
                ai_player = body.get("ai_player")
                if ai_player is not None and ai_player != "":
                    ai_player = int(ai_player)
                    if ai_player == 1: n2 = "AI (冠军)"
                    else: n1 = "AI (冠军)"
                else:
                    ai_player = None
                SESSION.reset((n1,n2), seed, first, sp, ai_player=ai_player)
                viewer = (1 - ai_player) if ai_player is not None else first
                self._json(200,{"ok":True,"state":serialize_state(viewer)}); return
            if u.path=="/api/action":
                r=_do_action(body.get("action",""), body.get("params",{}))
                r["state"]=serialize_state(int(body.get("viewer",0)))
                self._json(200,r); return
            if u.path=="/api/attack_decision":
                r=_advance_attack(body.get("answer"))
                r["state"]=serialize_state(int(body.get("viewer",0)))
                self._json(200,r); return
            if u.path=="/api/ai_turn":
                r = _run_ai_turn()
                r["state"]=serialize_state(int(body.get("viewer",0)))
                self._json(200,r); return
            self._json(404,{"ok":False,"message":"unknown"})

def serve(host="127.0.0.1", port=8000, open_browser=True):
    srv=ThreadingHTTPServer((host,port),Handler)
    url=f"http://{host}:{port}/"
    print(f"Poker Battle 服务已启动：{url}")
    if open_browser: threading.Thread(target=lambda:webbrowser.open(url),daemon=True).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\n已停止"); srv.server_close()
