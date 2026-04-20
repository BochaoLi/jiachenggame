/*
 * Poker Battle 1.8 · C++ Arena (v3)
 *
 * 用法: arena_v3.exe <strategies.json> <output.json> [matches_per_phase]
 *
 * - 读取 strategies.json 中的策略池
 * - 进行 matches_per_phase 次匹配（默认 1,000,000）
 * - 每次匹配 = 5 牌组 × 2 先后手 = 10 局
 * - 输出详细统计到 output.json（供外部模型分析）
 *
 * 输出内容包含：
 * - 每个策略的 Elo、胜率、胜/负/平场数
 * - 每个策略的统计：平均回合数、平均攻击值、平均防御翻牌数
 * - 全局统计：总对局数、总时间、速度
 * - 参数收敛分析数据
 *
 * 编译: g++ -O3 -std=c++17 -fopenmp -march=native "-Wl,--stack,8388608" -o arena_v3.exe arena_v3.cpp
 */

#include <iostream>
#include <fstream>
#include <sstream>
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

#ifdef _OPENMP
#include <omp.h>
#endif

// ============================================================================
// Card & Stack (same as arena_v2)
// ============================================================================
enum Suit : uint8_t { SPADES=0, HEARTS=1, CLUBS=2, DIAMONDS=3 };
struct Card { uint8_t suit, rank; };
static std::array<Card,52> FULL_DECK = [](){
    std::array<Card,52> d; int i=0;
    for(int s=0;s<4;s++) for(int r=1;r<=13;r++) d[i++]={(uint8_t)s,(uint8_t)r};
    return d;
}();
inline int base_value(Card c, bool ah){if(c.rank==1)return ah?11:1;return c.rank>=10?10:c.rank;}
inline int hq_penalty_value(Card c){if(c.rank==1)return 1;return c.rank>=10?5:c.rank;}
inline int discard_x(Card c, bool ah){if(c.rank==1)return ah?1:4;int v=c.rank>=10?10:c.rank;if(v<=5)return 3;if(v<=9)return 2;return 1;}

struct Stack {
    Card cards[52]; int sz=0;
    void push(Card c){cards[sz++]=c;}
    Card pop_front(){Card c=cards[0];for(int i=0;i<sz-1;i++)cards[i]=cards[i+1];sz--;return c;}
    bool empty()const{return sz==0;}
    void clear(){sz=0;}
    void shuffle(std::mt19937&rng){std::shuffle(cards,cards+sz,rng);}
    void remove_at(int i){for(int j=i;j<sz-1;j++)cards[j]=cards[j+1];sz--;}
};

// ============================================================================
// Strategy (23 parameters)
// ============================================================================
static constexpr int NUM_PARAMS = 23;
static const char* PARAM_NAMES[NUM_PARAMS] = {
    "w_attack","w_train","w_recruit","w_reorg","atk_min_value","atk_clubs_bonus",
    "deck_panic","hand_hungry","late_aggro_turn","ace_high_atk","ace_high_def","spade_double",
    "rescue_use","rescue_min_gap","bonus_fraction","deploy_ratio","deploy_clubs_focus","deploy_spread",
    "prepare_all","continue_def","prefer_hq","early_recruit_bonus","mid_attack_bonus"
};

struct Strategy {
    float params[NUM_PARAMS];
    std::string name;
    double elo = 1500.0;
    int wins=0, losses=0, draws=0, games=0;
    // Detailed stats
    double sum_turns=0, sum_atk_value=0, sum_def_cards=0, sum_attacks=0;
    int stat_n=0;

    float& p(int i){return params[i];}
    float p(int i)const{return params[i];}

    // Named accessors
    float w_attack()const{return params[0];}
    float w_train()const{return params[1];}
    float w_recruit()const{return params[2];}
    float w_reorg()const{return params[3];}
    float atk_min_value()const{return params[4];}
    float atk_clubs_bonus()const{return params[5];}
    float deck_panic()const{return params[6];}
    float hand_hungry()const{return params[7];}
    float late_aggro_turn()const{return params[8];}
    float ace_high_atk()const{return params[9];}
    float ace_high_def()const{return params[10];}
    float spade_double()const{return params[11];}
    float rescue_use()const{return params[12];}
    float rescue_min_gap()const{return params[13];}
    float bonus_fraction()const{return params[14];}
    float deploy_ratio()const{return params[15];}
    float deploy_clubs_focus()const{return params[16];}
    float deploy_spread()const{return params[17];}
    float prepare_all()const{return params[18];}
    float continue_def()const{return params[19];}
    float prefer_hq()const{return params[20];}
    float early_recruit_bonus()const{return params[21];}
    float mid_attack_bonus()const{return params[22];}
};

// ============================================================================
// Game (same core logic as arena_v2)
// ============================================================================
static constexpr int NUM_BF=3, MAX_BF=5;
struct Player { Stack hand,deck; Stack front[NUM_BF],back[NUM_BF]; };
struct GS {
    Player p[2]; Stack grave; int cur=0,turn=0,winner=-1; bool over=false; std::mt19937 rng;
    int n_atk=0; double total_atk_val=0, total_def_cards=0;
    void setup(uint32_t seed){
        rng.seed(seed); auto dk=FULL_DECK; std::shuffle(dk.begin(),dk.end(),rng);
        int idx=0;
        for(int pi=0;pi<2;pi++){
            p[pi].hand.sz=7;for(int i=0;i<7;i++)p[pi].hand.cards[i]=dk[idx++];
            p[pi].deck.sz=13;for(int i=0;i<13;i++)p[pi].deck.cards[i]=dk[idx++];
            for(int z=0;z<NUM_BF;z++){p[pi].front[z].clear();p[pi].back[z].clear();}
        }
        grave.sz=12;for(int i=0;i<12;i++)grave.cards[i]=dk[idx++];
        grave.shuffle(rng); cur=0;turn=1;winner=-1;over=false;n_atk=0;total_atk_val=0;total_def_cards=0;
    }
    void check(){if(over)return;for(int i=0;i<2;i++)if(p[i].deck.empty()){winner=1-i;over=true;return;}}
};

struct MatchResult { int wins_a=0, wins_b=0; int total_turns=0; double total_atk=0; double total_def=0; int total_attacks=0; };

// Play one game
int play_one(const Strategy& sa, const Strategy& sb, uint32_t seed, int& out_turns, double& out_atk, double& out_def, int& out_attacks){
    GS g; g.setup(seed);
    const Strategy* ss[2]={&sa,&sb};
    std::uniform_real_distribution<float> U(0,1);
    bool first_act[2]={true,true};

    // Initial deploy
    for(int pi=0;pi<2;pi++){
        const Strategy&s=*ss[pi]; Player&me=g.p[pi];
        int to_dep=std::max(1,(int)(me.hand.sz*s.deploy_ratio()));
        int caps[NUM_BF]={MAX_BF,MAX_BF,MAX_BF}; int dep=0; int az=0;
        for(int i=0;i<me.hand.sz&&dep<to_dep;){
            if(me.hand.cards[i].suit==CLUBS&&U(g.rng)<s.deploy_clubs_focus()&&caps[az]>0){
                me.back[az].push(me.hand.cards[i]);me.hand.remove_at(i);caps[az]--;dep++;
            }else i++;
        }
        for(int i=0;i<me.hand.sz&&dep<to_dep;){
            int tz=0;
            if(s.deploy_spread()>0.5f){for(int z=1;z<NUM_BF;z++)if(me.back[z].sz<me.back[tz].sz&&caps[z]>0)tz=z;}
            else{for(int z=1;z<NUM_BF;z++)if(caps[z]>caps[tz])tz=z;}
            if(caps[tz]<=0)break;
            me.back[tz].push(me.hand.cards[i]);me.hand.remove_at(i);caps[tz]--;dep++;
        }
    }

    for(int iter=0;iter<300&&!g.over;iter++){
        int cur=g.cur; const Strategy&my=*ss[cur]; const Strategy&opp=*ss[1-cur];
        Player&me=g.p[cur]; Player&enemy=g.p[1-cur];

        // Prepare
        if(U(g.rng)<my.prepare_all()){
            for(int z=0;z<NUM_BF;z++) while(!me.back[z].empty()&&me.front[z].sz<MAX_BF) me.front[z].push(me.back[z].pop_front());
        }

        // Actions
        int ap=first_act[cur]?1:2; first_act[cur]=false;
        bool used[5]={};
        float pmult=1.0f;
        if(g.turn>my.late_aggro_turn()) pmult+=my.mid_attack_bonus()*0.3f;

        for(int a=0;a<ap&&!g.over;a++){
            float sc[5]={-1,-1,-1,-1,-1};
            // Attack
            if(!used[0]){
                int bz=-1;float bv=0;
                for(int z=0;z<NUM_BF;z++){if(me.front[z].empty())continue;
                    int n=std::min(2,me.front[z].sz);float v=0;bool fc=(me.front[z].cards[0].suit==CLUBS);
                    for(int j=0;j<n;j++){int cv=base_value(me.front[z].cards[j],me.front[z].cards[j].rank==1);if(fc&&me.front[z].cards[j].suit==CLUBS)cv*=2;v+=cv;}
                    if(fc)v+=my.atk_clubs_bonus();
                    if(v>bv){bv=v;bz=z;}}
                if(bz>=0&&bv>=my.atk_min_value()) sc[0]=my.w_attack()*pmult;
            }
            if(!used[1]&&me.deck.sz>0){float b=me.hand.sz<my.hand_hungry()?3:0;sc[1]=my.w_train()+b;}
            if(!used[2]&&g.grave.sz>0){float b=me.deck.sz<my.deck_panic()?my.early_recruit_bonus()+2:0;if(g.turn<=3)b+=my.early_recruit_bonus();sc[2]=my.w_recruit()+b;}
            if(!used[3]){int ne=0;for(int z=0;z<NUM_BF;z++)if(!me.front[z].empty())ne++;if(ne>=2)sc[3]=my.w_reorg();}

            int best=-1;float bs=0;for(int i=0;i<5;i++)if(sc[i]>bs){bs=sc[i];best=i;}
            if(best<0)break; used[best]=true;

            if(best==0){ // Attack
                int zone=-1;float bv=0;
                for(int z=0;z<NUM_BF;z++){if(me.front[z].empty())continue;
                    int n=std::min(2,me.front[z].sz);float v=0;bool fc=(me.front[z].cards[0].suit==CLUBS);
                    for(int j=0;j<n;j++){int cv=base_value(me.front[z].cards[j],me.front[z].cards[j].rank==1);if(fc&&me.front[z].cards[j].suit==CLUBS)cv*=2;v+=cv;}
                    if(fc)v+=my.atk_clubs_bonus();if(v>bv){bv=v;zone=z;}}
                if(zone<0)break;
                int nf=std::min(2,me.front[zone].sz); Card ac[2];
                for(int i=0;i<nf;i++)ac[i]=me.front[zone].pop_front();
                bool fc=(ac[0].suit==CLUBS); int tatk=0;
                for(int i=0;i<nf;i++){bool ah=ac[i].rank==1?(U(g.rng)<my.ace_high_atk()):false;int v=base_value(ac[i],ah);if(fc&&ac[i].suit==CLUBS)v*=2;tatk+=v;}

                // Target
                bool thq=false,tback=false;
                if(!enemy.front[zone].empty()){}
                else if(!enemy.back[zone].empty()){thq=(U(g.rng)<my.prefer_hq());tback=!thq;}
                else thq=true;

                // Defense
                auto defend=[&](Stack&dt,bool ishq,bool isovf,int av)->int{
                    int acc=0; bool fsp=false; int spsum=0; bool rescued=false; int dcards=0;
                    while(acc<av&&!g.over){
                        Card dc; bool fromhq=false;
                        if(!ishq&&!dt.empty())dc=dt.pop_front();
                        else if(enemy.deck.sz>0){dc=enemy.deck.pop_front();fromhq=true;}
                        else{g.winner=cur;g.over=true;return-1;}
                        int dv;
                        if(fromhq){dv=hq_penalty_value(dc);}
                        else{bool dah=dc.rank==1?(U(g.rng)<opp.ace_high_def()):false;dv=base_value(dc,dah);
                            if(dc.suit==SPADES&&U(g.rng)<opp.spade_double())dv*=2;
                            if(acc==0&&!fromhq)fsp=(dc.suit==SPADES);
                            if(dc.suit==SPADES&&!fromhq)spsum+=dv;
                            if(dc.suit==HEARTS&&!rescued&&enemy.hand.sz>0&&U(g.rng)<opp.rescue_use()&&(av-acc)>opp.rescue_min_gap()){
                                Card rc=enemy.hand.pop_front();int rv=base_value(rc,rc.rank==1);
                                if(rc.suit==SPADES&&U(g.rng)<opp.spade_double())rv*=2;
                                acc+=rv;g.grave.push(rc);rescued=true;dcards++;}}
                        acc+=dv;g.grave.push(dc);dcards++;
                        if(enemy.deck.empty()){g.winner=cur;g.over=true;return-1;}
                        if(acc>=av&&!fromhq&&!dt.empty()&&U(g.rng)<opp.continue_def())acc=av-1;
                    }
                    g.total_def_cards+=dcards;
                    bool silenced=(!isovf&&fsp&&spsum>=av);
                    if(!silenced&&!g.over){
                        int hx=0,dx=0; Suit fs=(Suit)ac[0].suit;
                        for(int i=0;i<nf;i++){if(ac[i].suit==HEARTS&&fs==HEARTS)hx+=discard_x(ac[i],ac[i].rank==1);
                            if(ac[i].suit==DIAMONDS&&fs==DIAMONDS)dx+=discard_x(ac[i],ac[i].rank==1);}
                        hx=std::min(hx,6);dx=std::min(dx,6);
                        if(hx>0){int n=std::min(hx,me.deck.sz);for(int i=0;i<n;i++)me.hand.push(me.deck.pop_front());g.check();}
                        if(dx>0&&!g.over){int n=std::min(dx,g.grave.sz);for(int i=0;i<n;i++)me.deck.push(g.grave.pop_front());}
                    }
                    for(int i=0;i<nf;i++)g.grave.push(ac[i]);
                    return acc>=av?acc:-(av-acc);
                };

                int res;
                if(thq){Stack d;d.sz=0;res=defend(d,true,false,tatk);}
                else if(tback)res=defend(enemy.back[zone],false,false,tatk);
                else res=defend(enemy.front[zone],false,false,tatk);
                if(res<0&&!g.over){
                    int ovf=-res;
                    if(!thq&&!tback&&!enemy.back[zone].empty())defend(enemy.back[zone],false,true,ovf);
                    else if(!thq){Stack d;d.sz=0;defend(d,true,true,ovf);}
                }
                g.n_atk++;g.total_atk_val+=tatk;g.check();
            }else if(best==1){int n=std::min(2,me.deck.sz);for(int i=0;i<n;i++)me.hand.push(me.deck.pop_front());g.check();}
            else if(best==2){int n=std::min(2,g.grave.sz);for(int i=0;i<n;i++)me.deck.push(g.grave.pop_front());}
            else if(best==3){
                int za=-1,zb=-1;for(int z=0;z<NUM_BF;z++)if(!me.front[z].empty()){if(za<0)za=z;else if(zb<0)zb=z;}
                if(za>=0&&zb>=0&&std::abs(za-zb)==1){
                    Stack pool;pool.sz=0;for(int i=0;i<me.front[za].sz;i++)pool.push(me.front[za].cards[i]);
                    for(int i=0;i<me.front[zb].sz;i++)pool.push(me.front[zb].cards[i]);
                    me.front[za].clear();me.front[zb].clear();
                    for(int i=0;i<pool.sz;i++){if(pool.cards[i].suit==CLUBS&&me.front[za].sz<MAX_BF)me.front[za].push(pool.cards[i]);
                        else if(me.front[zb].sz<MAX_BF)me.front[zb].push(pool.cards[i]);else me.front[za].push(pool.cards[i]);}
                }
            }
        }
        if(g.over)break;

        // Deploy
        {int td=std::max(0,(int)(me.hand.sz*my.deploy_ratio()));int caps[NUM_BF];
            for(int z=0;z<NUM_BF;z++)caps[z]=MAX_BF-me.back[z].sz;
            int az=0;for(int z=1;z<NUM_BF;z++)if(caps[z]>caps[az])az=z;int dep=0;
            for(int i=0;i<me.hand.sz&&dep<td;){if(me.hand.cards[i].suit==CLUBS&&U(g.rng)<my.deploy_clubs_focus()&&caps[az]>0){me.back[az].push(me.hand.cards[i]);me.hand.remove_at(i);caps[az]--;dep++;}else i++;}
            for(int i=0;i<me.hand.sz&&dep<td;){int tz=0;if(my.deploy_spread()>0.5f){for(int z=1;z<NUM_BF;z++)if(me.back[z].sz<me.back[tz].sz&&caps[z]>0)tz=z;}else{for(int z=1;z<NUM_BF;z++)if(caps[z]>caps[tz])tz=z;}if(caps[tz]<=0)break;me.back[tz].push(me.hand.cards[i]);me.hand.remove_at(i);caps[tz]--;dep++;}
        }
        g.cur=1-g.cur;g.turn++;g.check();
    }
    out_turns=g.turn; out_atk=g.n_atk?g.total_atk_val/g.n_atk:0; out_def=g.n_atk?g.total_def_cards/g.n_atk:0; out_attacks=g.n_atk;
    return g.winner;
}

// 10-game match (5 seeds × 2 sides)
MatchResult play_match(const Strategy& a, const Strategy& b, uint32_t base_seed){
    MatchResult mr;
    for(int s=0;s<5;s++){
        uint32_t seed=base_seed+s*1000;
        for(int side=0;side<2;side++){
            int turns; double atk,def; int attacks;
            int w=play_one(side==0?a:b, side==0?b:a, seed+side, turns, atk, def, attacks);
            int wi=(w==0)?(side==0?0:1):(w==1)?(side==0?1:0):-1;
            if(wi==0)mr.wins_a++; else if(wi==1)mr.wins_b++;
            mr.total_turns+=turns; mr.total_atk+=atk; mr.total_def+=def; mr.total_attacks+=attacks;
        }
    }
    return mr;
}

// ============================================================================
// JSON I/O (minimal parser for strategy array)
// ============================================================================
std::string read_file(const std::string& path){
    std::ifstream f(path); std::stringstream ss; ss<<f.rdbuf(); return ss.str();
}

// Simple JSON strategy parser (expects specific format from Python)
std::vector<Strategy> parse_strategies(const std::string& json){
    std::vector<Strategy> out;
    // Find each strategy object
    size_t pos=0;
    while(true){
        pos=json.find("\"name\"",pos);
        if(pos==std::string::npos)break;
        Strategy s;
        // name
        size_t q1=json.find('\"',pos+6); size_t q2=json.find('\"',q1+1);
        s.name=json.substr(q1+1,q2-q1-1);
        // params
        for(int i=0;i<NUM_PARAMS;i++){
            std::string key="\""+std::string(PARAM_NAMES[i])+"\"";
            size_t kp=json.find(key,q2);
            if(kp==std::string::npos||kp>json.find('}',q2)+200) break;
            size_t cp=json.find(':',kp); size_t ve=json.find_first_of(",}",cp+1);
            s.params[i]=std::stof(json.substr(cp+1,ve-cp-1));
        }
        out.push_back(s);
        pos=q2+1;
    }
    return out;
}

void write_results(const std::string& path, const std::vector<Strategy>& pool, double time_sec, int total_matches){
    std::vector<int> ranked(pool.size()); std::iota(ranked.begin(),ranked.end(),0);
    std::sort(ranked.begin(),ranked.end(),[&](int a,int b){return pool[a].elo>pool[b].elo;});

    std::ofstream out(path); out<<std::fixed;
    out<<"{\n";
    out<<"  \"total_matches\":"<<total_matches<<",\n";
    out<<"  \"total_games\":"<<total_matches*10<<",\n";
    out<<"  \"time_seconds\":"<<std::setprecision(2)<<time_sec<<",\n";
    out<<"  \"matches_per_sec\":"<<std::setprecision(0)<<total_matches/time_sec<<",\n";
    out<<"  \"pool_size\":"<<pool.size()<<",\n";
    out<<"  \"strategies\":[\n";
    for(int ri=0;ri<(int)ranked.size();ri++){
        int idx=ranked[ri]; const auto&s=pool[idx];
        double avg_turns=s.stat_n?s.sum_turns/s.stat_n:0;
        double avg_atk=s.stat_n?s.sum_atk_value/s.stat_n:0;
        double avg_def=s.stat_n?s.sum_def_cards/s.stat_n:0;
        double avg_attacks=s.stat_n?s.sum_attacks/s.stat_n:0;
        double wr=s.games?100.0*s.wins/s.games:0;
        out<<"    {\"rank\":"<<ri+1<<",\"name\":\""<<s.name<<"\"";
        out<<",\"elo\":"<<std::setprecision(1)<<s.elo;
        out<<",\"wins\":"<<s.wins<<",\"losses\":"<<s.losses<<",\"draws\":"<<s.draws<<",\"games\":"<<s.games;
        out<<",\"win_rate\":"<<std::setprecision(1)<<wr;
        out<<",\"avg_turns\":"<<std::setprecision(1)<<avg_turns;
        out<<",\"avg_atk_value\":"<<std::setprecision(1)<<avg_atk;
        out<<",\"avg_def_cards\":"<<std::setprecision(2)<<avg_def;
        out<<",\"avg_attacks_per_game\":"<<std::setprecision(1)<<avg_attacks;
        out<<",\"params\":{";
        for(int i=0;i<NUM_PARAMS;i++){
            out<<"\""<<PARAM_NAMES[i]<<"\":"<<std::setprecision(4)<<s.params[i];
            if(i<NUM_PARAMS-1)out<<",";
        }
        out<<"}}";
        if(ri<(int)ranked.size()-1)out<<",";
        out<<"\n";
    }
    out<<"  ]\n}\n";
    out.close();
}

// ============================================================================
// Main: read strategies, run arena, write results
// ============================================================================
int main(int argc, char** argv){
    if(argc<3){std::cerr<<"Usage: arena_v3 <strategies.json> <output.json> [matches]\n";return 1;}
    std::string in_path=argv[1], out_path=argv[2];
    int total_matches = argc>3 ? std::atoi(argv[3]) : 1000000;

    std::cerr<<"Loading strategies from: "<<in_path<<std::endl;
    auto json = read_file(in_path);
    auto pool = parse_strategies(json);
    if(pool.empty()){std::cerr<<"ERROR: no strategies loaded\n";return 1;}
    std::cerr<<"Loaded "<<pool.size()<<" strategies"<<std::endl;
    std::cerr<<"Matches to play: "<<total_matches<<" ("<<total_matches*10<<" games)"<<std::endl;

    #ifdef _OPENMP
    std::cerr<<"OpenMP threads: "<<omp_get_max_threads()<<std::endl;
    #endif

    auto t0=std::chrono::high_resolution_clock::now();
    std::mt19937 master(12345);
    int n=pool.size();
    int report_every=std::max(1,total_matches/10);

    for(int m=0;m<total_matches;m++){
        // Pick pair (Elo-proximity matching 70%)
        std::uniform_int_distribution<int> pick(0,n-1);
        int si=pick(master),sj;
        if(std::uniform_real_distribution<float>(0,1)(master)<0.7f){
            std::vector<int> cands;
            for(int k=0;k<n;k++)if(k!=si&&std::abs(pool[k].elo-pool[si].elo)<250)cands.push_back(k);
            if(!cands.empty())sj=cands[std::uniform_int_distribution<int>(0,(int)cands.size()-1)(master)];
            else{do{sj=pick(master);}while(sj==si);}
        }else{do{sj=pick(master);}while(sj==si);}

        uint32_t seed=master();
        auto mr=play_match(pool[si],pool[sj],seed);

        // Determine winner
        int match_winner=-1;
        if(mr.wins_a>mr.wins_b) match_winner=0;
        else if(mr.wins_b>mr.wins_a) match_winner=1;

        // Update Elo
        double K=16.0;
        double ea=1.0/(1.0+std::pow(10.0,(pool[sj].elo-pool[si].elo)/400.0));
        double sa=(match_winner==0)?1.0:(match_winner==1)?0.0:0.5;
        pool[si].elo+=K*(sa-ea); pool[sj].elo+=K*((1-sa)-(1-ea));

        // Update stats
        if(match_winner==0){pool[si].wins++;pool[sj].losses++;}
        else if(match_winner==1){pool[sj].wins++;pool[si].losses++;}
        else{pool[si].draws++;pool[sj].draws++;}
        pool[si].games++; pool[sj].games++;

        double avg_t=(double)mr.total_turns/10.0;
        double avg_a=mr.total_attacks>0?mr.total_atk/mr.total_attacks:0;
        double avg_d=mr.total_attacks>0?mr.total_def/mr.total_attacks:0;
        double avg_atks=(double)mr.total_attacks/10.0;
        pool[si].sum_turns+=avg_t; pool[sj].sum_turns+=avg_t;
        pool[si].sum_atk_value+=avg_a; pool[sj].sum_atk_value+=avg_a;
        pool[si].sum_def_cards+=avg_d; pool[sj].sum_def_cards+=avg_d;
        pool[si].sum_attacks+=avg_atks; pool[sj].sum_attacks+=avg_atks;
        pool[si].stat_n++; pool[sj].stat_n++;

        if((m+1)%report_every==0){
            auto now=std::chrono::high_resolution_clock::now();
            double el=std::chrono::duration<double>(now-t0).count();
            double best_elo=-9999;std::string bn;
            for(auto&s:pool)if(s.elo>best_elo){best_elo=s.elo;bn=s.name;}
            std::cerr<<"["<<m+1<<"/"<<total_matches<<"] "<<std::fixed<<std::setprecision(0)<<(m+1)/el
                     <<" match/s, best="<<bn<<" "<<std::setprecision(1)<<best_elo<<std::endl;
        }
    }

    auto t1=std::chrono::high_resolution_clock::now();
    double total_time=std::chrono::duration<double>(t1-t0).count();

    write_results(out_path, pool, total_time, total_matches);
    std::cerr<<"\nDone: "<<total_matches<<" matches ("<<total_matches*10<<" games) in "
             <<std::setprecision(1)<<total_time<<"s ("<<std::setprecision(0)<<total_matches/total_time<<" match/s)\n";
    std::cerr<<"Result: "<<out_path<<"\n";
    return 0;
}
