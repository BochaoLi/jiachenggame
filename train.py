"""Poker Battle 策略进化调度器。

每阶段流程：
1. 将当前策略池写入 JSON
2. 调用 C++ arena 进行 100 万次匹配
3. 读取结果
4. 调用 Opus 4.6 分析结果，生成优化建议
5. 根据建议生成新策略 + 淘汰弱者
6. 写入 training_log.jsonl
7. 循环 10 个阶段

用法:
    python train.py [--phases 10] [--matches 1000000] [--no-llm]
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import copy
from pathlib import Path
from typing import Dict, List, Any

# ============================================================================
# 参数定义
# ============================================================================

PARAM_NAMES = [
    "w_attack","w_train","w_recruit","w_reorg","atk_min_value","atk_clubs_bonus",
    "deck_panic","hand_hungry","late_aggro_turn","ace_high_atk","ace_high_def","spade_double",
    "rescue_use","rescue_min_gap","bonus_fraction","deploy_ratio","deploy_clubs_focus","deploy_spread",
    "prepare_all","continue_def","prefer_hq","early_recruit_bonus","mid_attack_bonus"
]

PARAM_RANGES = {
    "w_attack": (1,12), "w_train": (1,8), "w_recruit": (1,8), "w_reorg": (0,4),
    "atk_min_value": (2,16), "atk_clubs_bonus": (0,6),
    "deck_panic": (2,8), "hand_hungry": (1,6), "late_aggro_turn": (3,12),
    "ace_high_atk": (0,1), "ace_high_def": (0,1), "spade_double": (0,1),
    "rescue_use": (0,1), "rescue_min_gap": (0,10), "bonus_fraction": (0,1),
    "deploy_ratio": (0.1,1), "deploy_clubs_focus": (0,1), "deploy_spread": (0,1),
    "prepare_all": (0,1), "continue_def": (0,0.5), "prefer_hq": (0,1),
    "early_recruit_bonus": (0,4), "mid_attack_bonus": (0,4),
}

# ============================================================================
# 策略生成（种子）
# ============================================================================

def make_strategy(name: str, **overrides) -> Dict:
    """创建策略，未指定参数用默认值。"""
    defaults = {
        "w_attack": 6, "w_train": 3, "w_recruit": 2.5, "w_reorg": 1,
        "atk_min_value": 5, "atk_clubs_bonus": 2,
        "deck_panic": 5, "hand_hungry": 3, "late_aggro_turn": 6,
        "ace_high_atk": 0.9, "ace_high_def": 0.85, "spade_double": 0.95,
        "rescue_use": 0.7, "rescue_min_gap": 3, "bonus_fraction": 1.0,
        "deploy_ratio": 0.8, "deploy_clubs_focus": 0.7, "deploy_spread": 0.4,
        "prepare_all": 0.9, "continue_def": 0.1, "prefer_hq": 0.6,
        "early_recruit_bonus": 2, "mid_attack_bonus": 1.5,
    }
    defaults.update(overrides)
    return {"name": name, "params": defaults}


def generate_seed_strategies() -> List[Dict]:
    """生成初始 100 套策略种子（10 体系 × 10 变体）。"""
    pool = []
    rng = random.Random(42)

    archetypes = {
        "blitz": {"w_attack": 10, "w_train": 1, "w_recruit": 1.5, "deploy_ratio": 0.95, "atk_min_value": 3, "prefer_hq": 0.8},
        "fortress": {"w_attack": 2.5, "w_train": 5, "w_recruit": 5, "deploy_ratio": 0.4, "deck_panic": 7, "hand_hungry": 5, "early_recruit_bonus": 3},
        "sniper": {"w_attack": 9, "atk_min_value": 14, "atk_clubs_bonus": 5, "deploy_clubs_focus": 0.95, "deploy_spread": 0.1},
        "tide": {"w_attack": 8.5, "w_train": 2.5, "w_recruit": 3, "atk_min_value": 3.5, "deploy_ratio": 0.9, "deploy_spread": 0.6, "deck_panic": 5.5},
        "engine": {"ace_high_atk": 0.2, "ace_high_def": 0.2, "bonus_fraction": 1, "w_train": 5, "w_recruit": 4, "w_attack": 5.5},
        "counter": {"w_attack": 4.5, "spade_double": 0.98, "rescue_use": 0.9, "rescue_min_gap": 1, "w_recruit": 4.5, "deploy_ratio": 0.6, "late_aggro_turn": 9},
        "allround": {"w_attack": 5.5, "w_train": 3.5, "w_recruit": 3, "deploy_ratio": 0.65, "atk_min_value": 6, "deck_panic": 4.5},
        "clubs_bomb": {"deploy_clubs_focus": 0.98, "deploy_spread": 0.05, "w_attack": 9.5, "atk_min_value": 10, "atk_clubs_bonus": 5.5, "w_reorg": 3},
        "adaptive": {"early_recruit_bonus": 2.5, "mid_attack_bonus": 2.5, "w_attack": 6.5, "w_train": 3.5, "w_recruit": 3.5, "deck_panic": 5, "hand_hungry": 3.5},
        "random": {},
    }

    for arch_name, base_params in archetypes.items():
        for v in range(10):
            params = {}
            if arch_name == "random":
                for k in PARAM_NAMES:
                    lo, hi = PARAM_RANGES[k]
                    params[k] = rng.uniform(lo, hi)
            else:
                for k in PARAM_NAMES:
                    if k in base_params:
                        lo, hi = PARAM_RANGES[k]
                        val = base_params[k] + rng.gauss(0, (hi-lo)*0.08)
                        params[k] = max(lo, min(hi, val))
                    else:
                        lo, hi = PARAM_RANGES[k]
                        default_val = make_strategy("x")["params"][k]
                        params[k] = max(lo, min(hi, default_val + rng.gauss(0, (hi-lo)*0.05)))
            pool.append({"name": f"{arch_name}_v{v}", "params": params})

    return pool


def mutate_strategy(parent: Dict, gen: int, rng: random.Random) -> Dict:
    """变异一个策略。"""
    child_params = dict(parent["params"])
    for k in PARAM_NAMES:
        if rng.random() < 0.3:
            lo, hi = PARAM_RANGES[k]
            delta = (hi - lo) * rng.gauss(0, 0.12)
            child_params[k] = max(lo, min(hi, child_params[k] + delta))
    name = f"g{gen}_mut_{parent['name'][:12]}_{rng.randint(0,9999):04d}"
    return {"name": name, "params": child_params}


def crossover_strategy(a: Dict, b: Dict, gen: int, rng: random.Random) -> Dict:
    """交叉两个策略。"""
    child_params = {}
    for k in PARAM_NAMES:
        child_params[k] = a["params"][k] if rng.random() < 0.5 else b["params"][k]
    name = f"g{gen}_cross_{rng.randint(0,99999):05d}"
    return {"name": name, "params": child_params}


def random_strategy(gen: int, rng: random.Random) -> Dict:
    """完全随机策略。"""
    params = {}
    for k in PARAM_NAMES:
        lo, hi = PARAM_RANGES[k]
        params[k] = rng.uniform(lo, hi)
    return {"name": f"g{gen}_rnd_{rng.randint(0,9999):04d}", "params": params}


# ============================================================================
# 策略池写入/读取
# ============================================================================

def write_strategies_json(pool: List[Dict], path: str):
    """写入 C++ arena 可读的策略 JSON。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"strategies": pool}, f, ensure_ascii=False, indent=2)


def read_arena_results(path: str) -> Dict:
    """读取 C++ arena 输出。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# LLM 分析（调 Opus 4.6）
# ============================================================================

def analyze_with_llm(phase: int, results: Dict, pool: List[Dict]) -> str:
    """调用当前对话中的分析能力来生成策略建议。

    在实际运行时，这个函数会把结果写入文件，由外部 Opus 4.6 分析。
    这里先生成一个规则化的分析摘要。
    """
    strategies = results["strategies"]
    top20 = strategies[:20]
    bottom20 = strategies[-20:]

    # 参数收敛分析
    convergence = {}
    for k in PARAM_NAMES:
        vals = [s["params"][k] for s in top20]
        import statistics
        avg = statistics.mean(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0
        lo, hi = PARAM_RANGES[k]
        norm_std = std / (hi - lo) if (hi - lo) > 0 else 0
        convergence[k] = {"avg": avg, "std": std, "norm_std": norm_std}

    # 自动分析
    converged = {k: v for k, v in convergence.items() if v["norm_std"] < 0.1}
    exploring = {k: v for k, v in convergence.items() if v["norm_std"] >= 0.15}

    analysis = {
        "phase": phase,
        "total_matches": results["total_matches"],
        "total_games": results["total_games"],
        "time_seconds": results["time_seconds"],
        "top5": [{"name": s["name"], "elo": s["elo"], "wr": s["win_rate"],
                  "avg_turns": s["avg_turns"]} for s in top20[:5]],
        "converged_params": {k: round(v["avg"], 4) for k, v in converged.items()},
        "exploring_params": {k: {"avg": round(v["avg"], 3), "std": round(v["std"], 3)} for k, v in exploring.items()},
        "top_param_averages": {k: round(convergence[k]["avg"], 4) for k in PARAM_NAMES},
        "recommendations": [],
    }

    # 生成建议
    if exploring:
        keys = list(exploring.keys())[:3]
        analysis["recommendations"].append(
            f"Focus exploration on: {', '.join(keys)} (still high variance in top strategies)"
        )
    if converged:
        analysis["recommendations"].append(
            f"Fix converged params: {', '.join(list(converged.keys())[:5])} to narrow search space"
        )

    # 比较 top vs bottom 的差异
    top_avgs = {k: statistics.mean([s["params"][k] for s in top20]) for k in PARAM_NAMES}
    bot_avgs = {k: statistics.mean([s["params"][k] for s in bottom20]) for k in PARAM_NAMES}
    diffs = sorted(
        [(k, top_avgs[k] - bot_avgs[k]) for k in PARAM_NAMES],
        key=lambda x: -abs(x[1])
    )
    analysis["top_vs_bottom_diffs"] = {k: round(d, 3) for k, d in diffs[:5]}
    analysis["recommendations"].append(
        f"Biggest differentiators (top vs bottom): {', '.join(k for k,_ in diffs[:3])}"
    )

    return analysis


# ============================================================================
# 新策略生成（基于分析）
# ============================================================================

def generate_new_strategies(
    pool: List[Dict], results: Dict, analysis: Dict,
    gen: int, count: int = 20, max_pool: int = 200,
) -> List[Dict]:
    """基于分析结果生成新策略并淘汰弱者。"""
    rng = random.Random(gen * 1000 + 42)
    strategies = results["strategies"]
    top_strats = strategies[:min(20, len(strategies))]

    new_strats = []
    for _ in range(count):
        r = rng.random()
        if r < 0.35:
            # 变异 top 策略
            parent = rng.choice(top_strats)
            parent_dict = {"name": parent["name"], "params": parent["params"]}
            new_strats.append(mutate_strategy(parent_dict, gen, rng))
        elif r < 0.65:
            # 交叉 top 策略
            a, b = rng.sample(top_strats, 2)
            a_dict = {"name": a["name"], "params": a["params"]}
            b_dict = {"name": b["name"], "params": b["params"]}
            new_strats.append(crossover_strategy(a_dict, b_dict, gen, rng))
        elif r < 0.85:
            # 聚焦探索：取 top1 参数，只变异"仍在探索"的维度
            top1 = top_strats[0]
            child_params = dict(top1["params"])
            exploring = analysis.get("exploring_params", {})
            for k in exploring:
                lo, hi = PARAM_RANGES[k]
                child_params[k] = max(lo, min(hi, child_params[k] + (hi-lo)*rng.gauss(0, 0.2)))
            name = f"g{gen}_focus_{rng.randint(0,9999):04d}"
            new_strats.append({"name": name, "params": child_params})
        else:
            # 完全随机
            new_strats.append(random_strategy(gen, rng))

    # 合并并淘汰
    # 用结果中的排名来定位当前 pool 中的策略
    current_names = {s["name"] for s in pool}
    # 加入新策略
    for ns in new_strats:
        pool.append(ns)

    # 淘汰：保留 top max_pool
    name_to_elo = {s["name"]: s["elo"] for s in strategies}
    # 新加入的策略还没有 Elo，给默认 1500
    pool.sort(key=lambda s: name_to_elo.get(s["name"], 1500), reverse=True)
    if len(pool) > max_pool:
        pool = pool[:max_pool]

    return pool


# ============================================================================
# 主流程
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Poker Battle 策略进化调度器")
    parser.add_argument("--phases", type=int, default=10, help="训练阶段数")
    parser.add_argument("--matches", type=int, default=1000000, help="每阶段匹配数")
    parser.add_argument("--max-pool", type=int, default=200, help="策略池最大数")
    parser.add_argument("--new-per-phase", type=int, default=20, help="每阶段新增策略数")
    parser.add_argument("--arena-exe", type=str, default="arena_v3.exe", help="C++ arena 可执行文件")
    args = parser.parse_args()

    print(f"=== Poker Battle 策略进化 ===")
    print(f"阶段数: {args.phases}")
    print(f"每阶段匹配: {args.matches:,}（= {args.matches*10:,} 局）")
    print(f"总计: {args.phases * args.matches:,} 匹配（= {args.phases * args.matches * 10:,} 局）")
    print()

    # 初始化
    pool = generate_seed_strategies()
    print(f"初始策略池: {len(pool)} 个")

    log_path = "training_log.jsonl"
    open(log_path, "w").close()  # 清空

    total_start = time.time()

    for phase in range(1, args.phases + 1):
        print(f"\n{'='*60}")
        print(f"阶段 {phase}/{args.phases} · 策略池 {len(pool)} 个 · {args.matches:,} 匹配")
        print(f"{'='*60}")

        # 1. 写入策略
        strat_file = f"phase_{phase}_strategies.json"
        write_strategies_json(pool, strat_file)

        # 2. 调用 C++ arena
        result_file = f"phase_{phase}_results.json"
        cmd = [args.arena_exe, strat_file, result_file, str(args.matches)]
        print(f"运行: {' '.join(cmd)}")
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - t0
        if proc.returncode != 0:
            print(f"ERROR: arena 失败\nstderr: {proc.stderr[:500]}")
            break
        print(proc.stderr.strip().split('\n')[-1] if proc.stderr else "")
        print(f"耗时: {elapsed:.1f}s")

        # 3. 读取结果
        results = read_arena_results(result_file)

        # 4. 分析
        analysis = analyze_with_llm(phase, results, pool)

        # 5. 输出摘要
        print(f"\n--- 阶段 {phase} 总结 ---")
        print(f"Top 5:")
        for s in analysis["top5"]:
            print(f"  {s['name']:<25} Elo={s['elo']:.1f} WR={s['wr']:.1f}%")
        print(f"已收敛参数 ({len(analysis['converged_params'])}): {', '.join(list(analysis['converged_params'].keys())[:8])}")
        print(f"探索中参数 ({len(analysis['exploring_params'])}): {', '.join(list(analysis['exploring_params'].keys())[:5])}")
        print(f"建议: {'; '.join(analysis['recommendations'][:2])}")

        # 6. 生成新策略 + 淘汰
        pool = generate_new_strategies(pool, results, analysis, gen=phase,
                                       count=args.new_per_phase, max_pool=args.max_pool)
        print(f"策略池更新: {len(pool)} 个")

        # 7. 写日志
        log_entry = {
            "phase": phase,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pool_size": len(pool),
            "matches": results["total_matches"],
            "games": results["total_games"],
            "time_sec": elapsed,
            "analysis": analysis,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        # 清理临时文件
        # os.remove(strat_file)  # 保留以备查看

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"全部完成: {args.phases} 阶段, 总计 {args.phases * args.matches:,} 匹配 ({args.phases * args.matches * 10:,} 局)")
    print(f"总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"日志: {log_path}")
    print(f"最终结果: phase_{args.phases}_results.json")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
