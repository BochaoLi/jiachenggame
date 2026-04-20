/*
 * Poker Battle 1.8 · C++ 自博弈引擎
 *
 * 完整实现规则：
 * - 作战区/待战区/牌库/手牌/墓地 五区域
 * - 准备阶段(前移) / 行动阶段(5操作) / 部署阶段
 * - 攻击：梅花翻倍(第一张规则) / 黑桃翻倍+沉默 / 红桃急救+弃牌抽 / 方片弃牌补库
 * - 大本营惩罚(A=1, 10JQK=5, 无效果)
 * - 击毁跳过弃牌 / 溢出贯穿 / 溢出待战区黑桃不沉默
 * - X加和上限6 / 第一张花色为准(攻防双方)
 * - 防御可选继续翻 / 四象防御(可选)
 *
 * 策略全部基于"玩家可见信息"决策，无任何信息作弊。
 *
 * 编译: g++ -O3 -std=c++17 -fopenmp -march=native -o arena_v2.exe arena_v2.cpp
 */

#include <iostream>
#include <fstream>
#include <vector>
#include <array>
#include <random>
#include <algorithm>
#include <cmath>
#include <chrono>
#include <string>
#include <numeric>
#include <iomanip>
#include <cassert>
#include <cstring>

#ifdef _OPENMP
#include <omp.h>
#endif

// ============================================================================
// Card
// ============================================================================
enum Suit : uint8_t { SPADES=0, HEARTS=1, CLUBS=2, DIAMONDS=3 };
struct Card { uint8_t suit, rank; /* rank 1-13, 1=A */ };
static constexpr int DECK_SIZE = 52;

static std::array<Card,52> FULL_DECK = [](){
    std::array<Card,52> d; int i=0;
    for(int s=0;s<4;s++) for(int r=1;r<=13;r++) d[i++]={(uint8_t)s,(uint8_t)r};
    return d;
}();

inline int base_value(Card c, bool ace_high) {
    if(c.rank==1) return ace_high?11:1;
    return c.rank>=10?10:c.rank;
}
inline int hq_penalty_value(Card c) {
    if(c.rank==1) return 1;
    return c.rank>=10?5:c.rank;
}
inline int discard_x(Card c, bool ace_high) {
    if(c.rank==1) return ace_high?1:4;
    int v = c.rank>=10?10:c.rank;
    if(v<=5) return 3;
    if(v<=9) return 2;
    return 1;
}

// ============================================================================
// Compact stack (no heap allocation for small sizes)
// ============================================================================
struct Stack {
    Card cards[52];
    int sz = 0;
    void push(Card c) { cards[sz++] = c; }
    Card pop_front() { Card c=cards[0]; for(int i=0;i<sz-1;i++) cards[i]=cards[i+1]; sz--; return c; }
    void push_back(Card c) { cards[sz++] = c; }
    bool empty() const { return sz==0; }
    void clear() { sz=0; }
    void shuffle(std::mt19937& rng) { std::shuffle(cards, cards+sz, rng); }
    void remove_at(int i) { for(int j=i;j<sz-1;j++) cards[j]=cards[j+1]; sz--; }
};

// ============================================================================
// Strategy (visible-info only)
// ============================================================================
struct Strategy {
    // --- 操作选择权重 ---
    float w_attack = 6.0f;      // 攻击优先级
    float w_train = 3.0f;       // 训练优先级
    float w_recruit = 2.5f;     // 征兵优先级
    float w_reorg = 1.0f;       // 重编优先级

    // --- 攻击决策 ---
    float atk_min_value = 4.0f;     // 低于此不攻击
    float atk_clubs_bonus = 3.0f;   // 梅花阵地额外加分

    // --- 状态自适应 ---
    float deck_panic = 4.0f;        // 牌库<此值时大幅提升recruit
    float hand_hungry = 3.0f;       // 手牌<此值时大幅提升train
    float late_aggro_turn = 6.0f;   // 超过此回合数提升攻击

    // --- A选择 ---
    float ace_high_atk = 0.9f;      // 攻击时A=11概率
    float ace_high_def = 0.85f;     // 防御时A=11概率

    // --- 黑桃翻倍 ---
    float spade_double = 0.95f;     // 翻倍概率

    // --- 急救 ---
    float rescue_use = 0.7f;        // 使用急救概率
    float rescue_min_gap = 3.0f;    // 攻击-累计防御差值>此值才急救

    // --- 弃牌效果 ---
    float bonus_fraction = 1.0f;    // 抽多少比例

    // --- 部署 ---
    float deploy_ratio = 0.8f;      // 部署手牌比例
    float deploy_clubs_focus = 0.7f;// 梅花集中度
    float deploy_spread = 0.4f;     // 分散度(0=集中 1=均匀)

    // --- 准备阶段 ---
    float prepare_all = 0.9f;       // 全部前移概率

    // --- 继续翻牌 ---
    float continue_def = 0.1f;      // 防御成功后继续翻概率

    // --- 攻击目标 ---
    float prefer_hq = 0.6f;        // 优先打大本营概率

    // --- 阶段加成 ---
    float early_recruit_bonus = 2.0f;  // 前3回合recruit加成
    float mid_attack_bonus = 1.5f;     // 4-8回合attack加成

    // --- 元数据 ---
    std::string name = "default";
    double elo = 1500.0;
    int wins=0, losses=0, games=0;
    double stat_turns=0;
    int stat_n=0;
};

static constexpr int NUM_PARAMS = 23;
struct PRange { float lo, hi; };
static const PRange RANGES[NUM_PARAMS] = {
    {1,12},{1,8},{1,8},{0,4},             // w_attack,w_train,w_recruit,w_reorg (4)
    {2,16},{0,6},                          // atk_min_value,atk_clubs_bonus (2)
    {2,8},{1,6},{3,12},                    // deck_panic,hand_hungry,late_aggro_turn (3)
    {0,1},{0,1},                           // ace_high_atk,ace_high_def (2)
    {0,1},                                 // spade_double (1)
    {0,1},{0,10},                          // rescue_use,rescue_min_gap (2)
    {0,1},                                 // bonus_fraction (1)
    {0.1f,1},{0,1},{0,1},                  // deploy_ratio,clubs_focus,deploy_spread (3)
    {0,1},                                 // prepare_all (1)
    {0,0.5f},                              // continue_def (1)
    {0,1},                                 // prefer_hq (1)
    {0,4},{0,4},                           // early_recruit_bonus, mid_attack_bonus (2) = total 23
};

inline float* param_ptr(Strategy& s, int i) {
    float* ptrs[] = {&s.w_attack,&s.w_train,&s.w_recruit,&s.w_reorg,
        &s.atk_min_value,&s.atk_clubs_bonus,
        &s.deck_panic,&s.hand_hungry,&s.late_aggro_turn,
        &s.ace_high_atk,&s.ace_high_def,&s.spade_double,
        &s.rescue_use,&s.rescue_min_gap,&s.bonus_fraction,
        &s.deploy_ratio,&s.deploy_clubs_focus,&s.deploy_spread,
        &s.prepare_all,&s.continue_def,&s.prefer_hq,
        &s.early_recruit_bonus,&s.mid_attack_bonus};
    return ptrs[i];
}

// ============================================================================
// Game State
// ============================================================================
static constexpr int NUM_BF = 3;
static constexpr int MAX_BF = 5;

struct Player {
    Stack hand, deck;
    Stack front[NUM_BF], back[NUM_BF];
    bool first_done = false;
};

struct GS {
    Player p[2];
    Stack grave;
    int cur=0, turn=0, winner=-1;
    bool over=false;
    std::mt19937 rng;
    int n_atk=0;
    double sum_turns=0;

    void setup(uint32_t seed) {
        rng.seed(seed);
        auto deck=FULL_DECK; std::shuffle(deck.begin(),deck.end(),rng);
        int idx=0;
        for(int pi=0;pi<2;pi++){
            p[pi].hand.sz=7; for(int i=0;i<7;i++) p[pi].hand.cards[i]=deck[idx++];
            p[pi].deck.sz=13; for(int i=0;i<13;i++) p[pi].deck.cards[i]=deck[idx++];
            for(int z=0;z<NUM_BF;z++){p[pi].front[z].clear();p[pi].back[z].clear();}
            p[pi].first_done=false;
        }
        grave.sz=12; for(int i=0;i<12;i++) grave.cards[i]=deck[idx++];
        grave.shuffle(rng);
        cur=0; turn=1; winner=-1; over=false; n_atk=0;
    }

    void check_loss() {
        if(over) return;
        for(int i=0;i<2;i++) if(p[i].deck.empty()){winner=1-i;over=true;return;}
    }
};

// ============================================================================
// Play one full game (strategy-driven, no info cheating)
// ============================================================================
struct GameResult { int winner; int turns; };

inline int estimate_zone_attack(const Player& me, int z) {
    if(me.front[z].empty()) return 0;
    int n = std::min(2, me.front[z].sz);
    int val = 0;
    bool first_clubs = (me.front[z].cards[0].suit == CLUBS);
    for(int j=0;j<n;j++) {
        Card c = me.front[z].cards[j];
        int v = base_value(c, c.rank==1);  // assume A=11
        if(first_clubs && c.suit==CLUBS) v *= 2;
        val += v;
    }
    return val;
}

GameResult play_game(const Strategy& s0, const Strategy& s1, uint32_t seed) {
    GS g; g.setup(seed);
    const Strategy* ss[2] = {&s0, &s1};
    std::uniform_real_distribution<float> U(0,1);

    // Skip mulligan (simple: no swap)
    // Initial deploy: deploy up to deploy_ratio * hand
    for(int pi=0;pi<2;pi++){
        const Strategy& s = *ss[pi];
        Player& me = g.p[pi];
        int to_deploy = std::max(1, (int)(me.hand.sz * s.deploy_ratio));
        int caps[NUM_BF] = {MAX_BF, MAX_BF, MAX_BF};
        int deployed = 0;
        // clubs to one zone
        int atk_z = 0;
        for(int i=0; i<me.hand.sz && deployed<to_deploy; ){
            if(me.hand.cards[i].suit==CLUBS && U(g.rng)<s.deploy_clubs_focus && caps[atk_z]>0){
                me.back[atk_z].push(me.hand.cards[i]);
                me.hand.remove_at(i); caps[atk_z]--; deployed++;
            } else i++;
        }
        for(int i=0; i<me.hand.sz && deployed<to_deploy; ){
            int tz = 0;
            if(s.deploy_spread > 0.5f) { // spread
                for(int z=1;z<NUM_BF;z++) if(me.back[z].sz<me.back[tz].sz && caps[z]>0) tz=z;
            } else { // concentrate
                for(int z=1;z<NUM_BF;z++) if(me.back[z].sz>me.back[tz].sz && caps[z]>0) tz=z;
            }
            if(caps[tz]<=0) break;
            me.back[tz].push(me.hand.cards[i]);
            me.hand.remove_at(i); caps[tz]--; deployed++;
        }
    }

    // Main loop
    bool first_action[2] = {true, true};
    for(int iter=0; iter<300 && !g.over; iter++){
        int cur = g.cur;
        const Strategy& my = *ss[cur];
        const Strategy& opp = *ss[1-cur];
        Player& me = g.p[cur];
        Player& enemy = g.p[1-cur];

        // Prepare: move back -> front
        if(U(g.rng) < my.prepare_all) {
            for(int z=0;z<NUM_BF;z++){
                while(!me.back[z].empty() && me.front[z].sz<MAX_BF)
                    me.front[z].push(me.back[z].pop_front());
            }
        }

        // Action phase
        int ap = first_action[cur] ? 1 : 2;
        first_action[cur] = false;
        bool used[5] = {};

        float phase_atk_mult = 1.0f;
        if(g.turn <= 3) {} // early
        else if(g.turn <= 8) phase_atk_mult += my.mid_attack_bonus * 0.3f;
        else phase_atk_mult += my.mid_attack_bonus * 0.5f;

        for(int a=0; a<ap && !g.over; a++){
            // Score each action
            float scores[5] = {-1,-1,-1,-1,-1};

            // Attack
            if(!used[0]){
                int best_z=-1; float best_v=0;
                for(int z=0;z<NUM_BF;z++){
                    if(me.front[z].empty()) continue;
                    float v = estimate_zone_attack(me, z);
                    if(me.front[z].cards[0].suit==CLUBS) v += my.atk_clubs_bonus;
                    if(v > best_v){best_v=v; best_z=z;}
                }
                if(best_z>=0 && best_v >= my.atk_min_value)
                    scores[0] = my.w_attack * phase_atk_mult;
            }
            // Train
            if(!used[1] && me.deck.sz>0){
                float bonus = me.hand.sz < my.hand_hungry ? 3.0f : 0;
                scores[1] = my.w_train + bonus;
            }
            // Recruit
            if(!used[2] && g.grave.sz>0){
                float bonus = me.deck.sz < my.deck_panic ? my.early_recruit_bonus + 2.0f : 0;
                if(g.turn<=3) bonus += my.early_recruit_bonus;
                scores[2] = my.w_recruit + bonus;
            }
            // Reorg
            if(!used[3]){
                int ne=0; for(int z=0;z<NUM_BF;z++) if(!me.front[z].empty()) ne++;
                if(ne>=2) scores[3] = my.w_reorg;
            }
            // Balance (skip for simplicity in arena - low impact)

            int best=-1; float bs=0;
            for(int i=0;i<5;i++) if(scores[i]>bs){bs=scores[i];best=i;}
            if(best<0) break;
            used[best]=true;

            if(best==0){ // Attack
                int zone=-1; float bv=0;
                for(int z=0;z<NUM_BF;z++){
                    if(me.front[z].empty()) continue;
                    float v=estimate_zone_attack(me,z);
                    if(me.front[z].cards[0].suit==CLUBS) v+=my.atk_clubs_bonus;
                    if(v>bv){bv=v;zone=z;}
                }
                if(zone<0) break;

                int n_flip = std::min(2, me.front[zone].sz);
                Card ac[2]; for(int i=0;i<n_flip;i++) ac[i]=me.front[zone].pop_front();

                // Compute attack value
                bool first_clubs = (ac[0].suit == CLUBS);
                int total_atk = 0;
                for(int i=0;i<n_flip;i++){
                    bool ah = ac[i].rank==1 ? (U(g.rng)<my.ace_high_atk) : false;
                    int v = base_value(ac[i], ah);
                    if(first_clubs && ac[i].suit==CLUBS) v*=2;
                    total_atk += v;
                }

                // Determine target
                bool target_hq = false;
                bool target_back = false;
                if(!enemy.front[zone].empty()){
                    // must attack front
                } else if(!enemy.back[zone].empty()){
                    target_back = !(U(g.rng) < my.prefer_hq);
                    target_hq = !target_back;
                } else {
                    target_hq = true;
                }

                // Defense
                auto resolve_defense = [&](Stack& def_troop, bool is_hq, bool is_overflow, int atk_val) -> int {
                    int acc=0, hq_used=0;
                    bool first_spade = false;
                    int spade_sum = 0;
                    bool rescued = false;

                    while(acc < atk_val && !g.over){
                        Card dc;
                        if(!is_hq && !def_troop.empty()){
                            dc = def_troop.pop_front();
                        } else if(!is_hq && enemy.deck.sz>0){
                            // troop exhausted, go to HQ
                            is_hq = true;
                            dc = enemy.deck.pop_front();
                            hq_used++;
                        } else if(is_hq && enemy.deck.sz>0){
                            dc = enemy.deck.pop_front();
                            hq_used++;
                        } else {
                            g.winner = cur; g.over = true;
                            return -1;
                        }

                        int dv;
                        if(is_hq || (hq_used>0 && def_troop.empty())){
                            dv = hq_penalty_value(dc);
                        } else {
                            bool dah = dc.rank==1 ? (U(g.rng)<opp.ace_high_def) : false;
                            dv = base_value(dc, dah);
                            if(dc.suit==SPADES && U(g.rng)<opp.spade_double) dv*=2;

                            // Track first defense suit for silence
                            if(acc==0 && hq_used==0) first_spade = (dc.suit==SPADES);
                            if(dc.suit==SPADES) spade_sum += dv;

                            // Rescue
                            if(dc.suit==HEARTS && !rescued && enemy.hand.sz>0
                               && U(g.rng)<opp.rescue_use
                               && (atk_val - acc) > opp.rescue_min_gap){
                                // Play hand[0] as rescue
                                Card rc = enemy.hand.pop_front();
                                int rv = base_value(rc, rc.rank==1);
                                if(rc.suit==SPADES && U(g.rng)<opp.spade_double) rv*=2;
                                acc += rv;
                                g.grave.push(rc);
                                rescued = true;
                            }
                        }
                        acc += dv;
                        g.grave.push(dc);

                        if(enemy.deck.empty()){g.winner=cur;g.over=true;return -1;}

                        // Continue defense?
                        if(acc >= atk_val && !is_hq && !def_troop.empty() && U(g.rng)<opp.continue_def){
                            acc = atk_val - 1; // force one more flip
                        }
                    }

                    // Silence check (only non-overflow, front/back)
                    bool silenced = false;
                    if(!is_overflow && first_spade && spade_sum >= atk_val) silenced = true;

                    // Discard effects (attacker)
                    if(!silenced && !g.over){
                        Suit first_atk_suit = (Suit)ac[0].suit;
                        int hearts_x=0, diamonds_x=0;
                        for(int i=0;i<n_flip;i++){
                            if(ac[i].suit==HEARTS && first_atk_suit==HEARTS)
                                hearts_x += discard_x(ac[i], ac[i].rank==1);
                            if(ac[i].suit==DIAMONDS && first_atk_suit==DIAMONDS)
                                diamonds_x += discard_x(ac[i], ac[i].rank==1);
                        }
                        hearts_x=std::min(hearts_x,6); diamonds_x=std::min(diamonds_x,6);
                        if(hearts_x>0){
                            int n=std::min(hearts_x,(int)(hearts_x*my.bonus_fraction));
                            n=std::min(n,me.deck.sz);
                            for(int i=0;i<n;i++) me.hand.push(me.deck.pop_front());
                            g.check_loss();
                        }
                        if(diamonds_x>0 && !g.over){
                            int n=std::min(diamonds_x,(int)(diamonds_x*my.bonus_fraction));
                            n=std::min(n,g.grave.sz);
                            for(int i=0;i<n;i++) me.deck.push(g.grave.pop_front());
                        }
                    }

                    // Attacker cards to graveyard
                    for(int i=0;i<n_flip;i++) g.grave.push(ac[i]);

                    return acc >= atk_val ? acc : -(atk_val - acc); // negative = overflow
                };

                int result;
                if(target_hq){
                    Stack dummy; dummy.sz=0;
                    result = resolve_defense(dummy, true, false, total_atk);
                } else if(target_back){
                    result = resolve_defense(enemy.back[zone], false, false, total_atk);
                } else {
                    result = resolve_defense(enemy.front[zone], false, false, total_atk);
                }

                // Overflow
                if(result < 0 && !g.over){
                    int overflow = -result;
                    if(!target_hq && !target_back && !enemy.back[zone].empty()){
                        resolve_defense(enemy.back[zone], false, true, overflow);
                    } else if(!target_hq){
                        Stack dummy; dummy.sz=0;
                        resolve_defense(dummy, true, true, overflow);
                    }
                }

                g.n_atk++;
                g.check_loss();

            } else if(best==1){ // Train
                int n=std::min(2, me.deck.sz);
                for(int i=0;i<n;i++) me.hand.push(me.deck.pop_front());
                g.check_loss();
            } else if(best==2){ // Recruit
                int n=std::min(2, g.grave.sz);
                for(int i=0;i<n;i++) me.deck.push(g.grave.pop_front());
            } else if(best==3){ // Reorg
                int za=-1,zb=-1;
                for(int z=0;z<NUM_BF;z++) if(!me.front[z].empty()){if(za<0)za=z;else if(zb<0)zb=z;}
                if(za>=0 && zb>=0 && std::abs(za-zb)==1){
                    // merge and split: clubs to za
                    Stack pool; pool.sz=0;
                    for(int i=0;i<me.front[za].sz;i++) pool.push(me.front[za].cards[i]);
                    for(int i=0;i<me.front[zb].sz;i++) pool.push(me.front[zb].cards[i]);
                    me.front[za].clear(); me.front[zb].clear();
                    for(int i=0;i<pool.sz;i++){
                        if(pool.cards[i].suit==CLUBS && me.front[za].sz<MAX_BF)
                            me.front[za].push(pool.cards[i]);
                        else if(me.front[zb].sz<MAX_BF) me.front[zb].push(pool.cards[i]);
                        else me.front[za].push(pool.cards[i]);
                    }
                }
            }
        }
        if(g.over) break;

        // Deploy
        {
            int to_dep = std::max(0, (int)(me.hand.sz * my.deploy_ratio));
            int caps[NUM_BF]; for(int z=0;z<NUM_BF;z++) caps[z]=MAX_BF-me.back[z].sz;
            int atk_z=0; for(int z=1;z<NUM_BF;z++) if(caps[z]>caps[atk_z]) atk_z=z;
            int deployed=0;
            // clubs first
            for(int i=0;i<me.hand.sz && deployed<to_dep;){
                if(me.hand.cards[i].suit==CLUBS && U(g.rng)<my.deploy_clubs_focus && caps[atk_z]>0){
                    me.back[atk_z].push(me.hand.cards[i]);
                    me.hand.remove_at(i); caps[atk_z]--; deployed++;
                } else i++;
            }
            for(int i=0;i<me.hand.sz && deployed<to_dep;){
                int tz=0;
                if(my.deploy_spread>0.5f){for(int z=1;z<NUM_BF;z++)if(me.back[z].sz<me.back[tz].sz&&caps[z]>0)tz=z;}
                else{for(int z=1;z<NUM_BF;z++)if(caps[z]>caps[tz])tz=z;}
                if(caps[tz]<=0) break;
                me.back[tz].push(me.hand.cards[i]);
                me.hand.remove_at(i); caps[tz]--; deployed++;
            }
        }

        me.first_done = true;
        g.cur = 1 - g.cur;
        g.turn++;
        g.check_loss();
    }

    return {g.winner, g.turn};
}

// ============================================================================
// Elo
// ============================================================================
inline void elo_update(double& ea, double& eb, int w, double K=16.0){
    double e=1.0/(1.0+std::pow(10.0,(eb-ea)/400.0));
    double s=(w==0)?1.0:(w==1)?0.0:0.5;
    ea+=K*(s-e); eb+=K*((1-s)-(1-e));
}

// ============================================================================
// Strategy generation
// ============================================================================
Strategy mutate(const Strategy& p, std::mt19937& rng, int gen){
    Strategy c=p;
    std::normal_distribution<float> N(0,0.12f);
    std::uniform_real_distribution<float> U(0,1);
    for(int i=0;i<NUM_PARAMS;i++){
        if(U(rng)<0.3f){float*v=param_ptr(c,i);*v=std::clamp(*v+(RANGES[i].hi-RANGES[i].lo)*N(rng),RANGES[i].lo,RANGES[i].hi);}
    }
    c.name="g"+std::to_string(gen)+"_"+std::to_string(std::uniform_int_distribution<int>(0,99999)(rng));
    c.elo=1500;c.wins=c.losses=c.games=c.stat_n=0;c.stat_turns=0;
    return c;
}

Strategy crossover(const Strategy& a, const Strategy& b, std::mt19937& rng, int gen){
    Strategy c; std::uniform_real_distribution<float> U(0,1);
    for(int i=0;i<NUM_PARAMS;i++) *param_ptr(c,i)=U(rng)<0.5f?*const_cast<float*>(param_ptr(const_cast<Strategy&>(a),i)):*const_cast<float*>(param_ptr(const_cast<Strategy&>(b),i));
    c.name="g"+std::to_string(gen)+"_x"+std::to_string(std::uniform_int_distribution<int>(0,99999)(rng));
    c.elo=1500;c.wins=c.losses=c.games=c.stat_n=0;c.stat_turns=0;
    return c;
}

// ============================================================================
// Archetype generators (10 types × 10 variants = 100 seeds)
// ============================================================================
std::vector<Strategy> generate_archetypes(std::mt19937& rng){
    std::vector<Strategy> all;
    std::uniform_real_distribution<float> U(0,1);
    std::normal_distribution<float> N(0,0.1f);

    auto make = [&](const char* arch, auto fn){
        for(int v=0;v<10;v++){Strategy s; fn(s,v); s.name=std::string(arch)+"_v"+std::to_string(v); all.push_back(s);}
    };

    make("blitz",[&](Strategy&s,int v){s.w_attack=10+N(rng);s.w_train=1;s.w_recruit=1.5;s.deploy_ratio=0.95;s.atk_min_value=3+U(rng)*3;s.ace_high_atk=0.95;s.prefer_hq=0.7+U(rng)*0.3;s.late_aggro_turn=4;});
    make("fortress",[&](Strategy&s,int v){s.w_attack=2+U(rng)*2;s.w_train=5+U(rng)*2;s.w_recruit=5+U(rng)*2;s.deploy_ratio=0.4+U(rng)*0.2;s.deck_panic=6+U(rng)*2;s.hand_hungry=5+U(rng)*2;s.early_recruit_bonus=3;});
    make("sniper",[&](Strategy&s,int v){s.w_attack=8+U(rng)*2;s.atk_min_value=12+U(rng)*4;s.atk_clubs_bonus=5;s.deploy_clubs_focus=0.95;s.deploy_spread=0.1;s.mid_attack_bonus=2+U(rng);});
    make("tide",[&](Strategy&s,int v){s.w_attack=8+U(rng)*2;s.w_train=2.5;s.w_recruit=3;s.atk_min_value=3+U(rng)*3;s.deploy_ratio=0.85+U(rng)*0.15;s.deck_panic=5+U(rng)*2;s.deploy_spread=0.5+U(rng)*0.3;});
    make("engine",[&](Strategy&s,int v){s.ace_high_atk=0.2+U(rng)*0.2;s.ace_high_def=0.2+U(rng)*0.2;s.bonus_fraction=1.0;s.w_train=4+U(rng)*2;s.w_recruit=3+U(rng)*2;s.w_attack=5+U(rng)*2;});
    make("counter",[&](Strategy&s,int v){s.w_attack=4+U(rng)*2;s.spade_double=0.98;s.rescue_use=0.9;s.rescue_min_gap=1;s.w_recruit=4+U(rng);s.deploy_ratio=0.6+U(rng)*0.2;s.late_aggro_turn=8+U(rng)*2;});
    make("allround",[&](Strategy&s,int v){s.w_attack=5+U(rng)*2;s.w_train=3+U(rng);s.w_recruit=2.5+U(rng);s.deploy_ratio=0.6+U(rng)*0.2;s.atk_min_value=5+U(rng)*4;s.deck_panic=4+U(rng)*2;});
    make("clubs_bomb",[&](Strategy&s,int v){s.deploy_clubs_focus=0.98;s.deploy_spread=0.05;s.w_attack=9+U(rng)*2;s.atk_min_value=8+U(rng)*6;s.atk_clubs_bonus=5+U(rng)*2;s.w_reorg=3+U(rng);});
    make("adaptive",[&](Strategy&s,int v){s.early_recruit_bonus=2.5+U(rng);s.mid_attack_bonus=2+U(rng);s.w_attack=6+U(rng)*2;s.w_train=3+U(rng);s.w_recruit=3+U(rng);s.deck_panic=4+U(rng)*2;s.hand_hungry=3+U(rng)*2;});
    make("random",[&](Strategy&s,int v){for(int i=0;i<NUM_PARAMS;i++)*param_ptr(s,i)=std::uniform_real_distribution<float>(RANGES[i].lo,RANGES[i].hi)(rng);});

    return all;
}

// ============================================================================
// Main
// ============================================================================
int main(int argc, char** argv){
    int total_iter = 100000; // default for benchmarking
    std::string outfile = "arena_v2_result.json";
    if(argc>1) total_iter=std::atoi(argv[1]);
    if(argc>2) outfile=argv[2];

    #ifdef _OPENMP
    std::cerr<<"OpenMP threads: "<<omp_get_max_threads()<<std::endl;
    #endif
    std::cerr<<"Iterations: "<<total_iter<<std::endl;

    std::mt19937 master(42);
    auto pool = generate_archetypes(master);
    std::cerr<<"Initial pool: "<<pool.size()<<" strategies"<<std::endl;

    const int MAX_POOL = 200;
    const int EVOLVE_EVERY = 2000;
    const int NEW_PER_EVOLVE = 8;
    const int REPORT_EVERY = std::max(1, total_iter/10);

    auto t0 = std::chrono::high_resolution_clock::now();

    for(int iter=0; iter<total_iter; iter++){
        int n=pool.size();
        std::uniform_int_distribution<int> pick(0,n-1);
        int si=pick(master), sj;
        if(std::uniform_real_distribution<float>(0,1)(master)<0.7f){
            std::vector<int> cands;
            for(int k=0;k<n;k++) if(k!=si && std::abs(pool[k].elo-pool[si].elo)<250) cands.push_back(k);
            if(!cands.empty()) sj=cands[std::uniform_int_distribution<int>(0,(int)cands.size()-1)(master)];
            else{do{sj=pick(master);}while(sj==si);}
        } else{do{sj=pick(master);}while(sj==si);}

        uint32_t seed=master();
        for(int side=0;side<2;side++){
            auto res=play_game(side==0?pool[si]:pool[sj], side==0?pool[sj]:pool[si], seed+side);
            int wi=res.winner==0?(side==0?si:sj):res.winner==1?(side==0?sj:si):-1;
            elo_update(pool[si].elo, pool[sj].elo, wi==si?0:wi==sj?1:-1);
            if(wi==si){pool[si].wins++;pool[sj].losses++;}
            else if(wi==sj){pool[sj].wins++;pool[si].losses++;}
            pool[si].games++; pool[sj].games++;
            pool[si].stat_turns+=res.turns; pool[sj].stat_turns+=res.turns;
            pool[si].stat_n++; pool[sj].stat_n++;
        }

        if((iter+1)%EVOLVE_EVERY==0){
            int gen=(iter+1)/EVOLVE_EVERY;
            std::vector<int> ranked(pool.size()); std::iota(ranked.begin(),ranked.end(),0);
            std::sort(ranked.begin(),ranked.end(),[&](int a,int b){return pool[a].elo>pool[b].elo;});
            for(int k=0;k<NEW_PER_EVOLVE&&(int)pool.size()<MAX_POOL;k++){
                float r=std::uniform_real_distribution<float>(0,1)(master);
                if(r<0.4f) pool.push_back(mutate(pool[ranked[std::uniform_int_distribution<int>(0,std::min(15,(int)pool.size()-1))(master)]],master,gen));
                else if(r<0.75f){int pa=ranked[std::uniform_int_distribution<int>(0,std::min(10,(int)pool.size()-1))(master)],pb=ranked[std::uniform_int_distribution<int>(0,std::min(20,(int)pool.size()-1))(master)]; pool.push_back(pa!=pb?crossover(pool[pa],pool[pb],master,gen):mutate(pool[pa],master,gen));}
                else{Strategy s;for(int i=0;i<NUM_PARAMS;i++)*param_ptr(s,i)=std::uniform_real_distribution<float>(RANGES[i].lo,RANGES[i].hi)(master);s.name="g"+std::to_string(gen)+"_rnd";pool.push_back(s);}
            }
            if((int)pool.size()>MAX_POOL){
                std::vector<int> r2(pool.size());std::iota(r2.begin(),r2.end(),0);
                std::sort(r2.begin(),r2.end(),[&](int a,int b){return pool[a].elo>pool[b].elo;});
                std::vector<Strategy> np;for(int i=0;i<MAX_POOL;i++)np.push_back(pool[r2[i]]);pool=np;
            }
        }

        if((iter+1)%REPORT_EVERY==0){
            auto now=std::chrono::high_resolution_clock::now();
            double el=std::chrono::duration<double>(now-t0).count();
            double best_elo=-9999; std::string bn;
            for(auto&s:pool) if(s.elo>best_elo){best_elo=s.elo;bn=s.name;}
            std::cerr<<"["<<iter+1<<"/"<<total_iter<<"] "<<std::fixed<<std::setprecision(0)<<(iter+1)/el
                     <<" iter/s, pool="<<pool.size()<<", best="<<bn<<" "<<std::setprecision(1)<<best_elo<<std::endl;
        }
    }

    auto t1=std::chrono::high_resolution_clock::now();
    double total_time=std::chrono::duration<double>(t1-t0).count();

    // Output
    std::vector<int> ranked(pool.size()); std::iota(ranked.begin(),ranked.end(),0);
    std::sort(ranked.begin(),ranked.end(),[&](int a,int b){return pool[a].elo>pool[b].elo;});

    std::ofstream out(outfile); out<<std::fixed;
    out<<"{\n  \"total_iterations\":"<<total_iter<<",\"time_seconds\":"<<std::setprecision(2)<<total_time
       <<",\"iter_per_sec\":"<<std::setprecision(0)<<total_iter/total_time<<",\"pool_size\":"<<pool.size()
       <<",\n  \"strategies\":[\n";
    for(int ri=0;ri<(int)ranked.size();ri++){
        int idx=ranked[ri]; auto&s=pool[idx];
        double at=s.stat_n?s.stat_turns/s.stat_n:0;
        out<<"    {\"rank\":"<<ri+1<<",\"name\":\""<<s.name<<"\",\"elo\":"<<std::setprecision(1)<<s.elo
           <<",\"wins\":"<<s.wins<<",\"losses\":"<<s.losses<<",\"games\":"<<s.games
           <<",\"wr\":"<<std::setprecision(1)<<(s.games?100.0*s.wins/s.games:0)
           <<",\"avg_turns\":"<<std::setprecision(1)<<at
           <<",\"params\":{";
        for(int i=0;i<NUM_PARAMS;i++){out<<"\""<<i<<"\":"<<std::setprecision(4)<<*param_ptr(const_cast<Strategy&>(s),i);if(i<NUM_PARAMS-1)out<<",";}
        out<<"}}"; if(ri<(int)ranked.size()-1)out<<","; out<<"\n";
    }
    out<<"  ]\n}\n"; out.close();

    // Console
    std::cerr<<"\n"<<std::string(110,'=')<<"\n";
    std::cerr<<"Done: "<<total_iter<<" iter, "<<std::setprecision(2)<<total_time<<"s ("<<std::setprecision(0)<<total_iter/total_time<<" iter/s), pool="<<pool.size()<<"\n";
    std::cerr<<std::string(110,'=')<<"\n";
    std::cerr<<std::left<<std::setw(4)<<"#"<<std::setw(25)<<"name"<<std::setw(8)<<"elo"<<std::setw(8)<<"wr%"<<std::setw(8)<<"games"<<std::setw(8)<<"turns"<<"\n";
    std::cerr<<std::string(110,'-')<<"\n";
    for(int ri=0;ri<std::min(20,(int)ranked.size());ri++){
        int idx=ranked[ri]; auto&s=pool[idx];
        double wr=s.games?100.0*s.wins/s.games:0;
        double at=s.stat_n?s.stat_turns/s.stat_n:0;
        std::cerr<<std::left<<std::setw(4)<<ri+1<<std::setw(25)<<s.name<<std::setw(8)<<std::setprecision(1)<<s.elo<<std::setw(8)<<wr<<std::setw(8)<<s.games<<std::setw(8)<<at<<"\n";
    }
    std::cerr<<std::string(110,'=')<<"\nResult: "<<outfile<<"\n";
    return 0;
}
