# Poker Battle 1.8 · 测试报告

## 运行方式

```bash
python -m unittest discover -s tests -v
```

当前状态：**47 个测试全部通过**。

---

## 测试覆盖一览

共 **14 个测试类**，**47 个用例**，覆盖了全部基础规则和大部分特殊规则。


| #   | 测试类                     | 用例数 | 覆盖规则                                        |
| --- | ----------------------- | --- | ------------------------------------------- |
| 1   | SetupTests              | 4   | 发牌、换牌（含上限3验证）、初始部署                          |
| 2   | PrepareTests            | 3   | 全部前移、部分前移、不移动                               |
| 3   | ActionTests             | 5   | 征兵、训练、不可重复、首回合1点、次回合2点                      |
| 4   | BalanceTests            | 4   | 制衡模式A（手牌↔牌库）、模式B（手牌↔墓地）、上限验证                |
| 5   | HQPenaltyValueTests     | 2   | 大本营惩罚点数(A=1,10JQK=5)、X值计算                   |
| 6   | AttackTests             | 8   | 梅花翻倍、第一张规则、大本营惩罚/败北、击毁跳过弃牌、沉默、重编相邻限制、黑桃可选翻倍 |
| 7   | OverflowTests           | 3   | 溢出到大本营、溢出到待战区、溢出导致败北                        |
| 8   | RescueTests             | 2   | 急救加入防御、急救每轮限一次                              |
| 9   | DiscardEffectTests      | 4   | 红桃抽牌、方片抽牌、第一张花色限制、X上限6                      |
| 10  | ContinueDefenseTests    | 1   | 防御成功后可选继续翻                                  |
| 11  | AttackTargetTests       | 3   | 有部队必攻前排、选攻大本营、选攻待战区                         |
| 12  | FourElementDefenseTests | 2   | 四象防御触发、默认关闭                                 |
| 13  | DeployTests             | 3   | 部署到待战区、超限报错、牌不在手中报错                         |
| 14  | FullGameFlowTest        | 2   | 完整多回合流程、牌库耗尽检测                              |


---

## 各测试详细说明

### 1. SetupTests（初始化配置）


| 用例                        | 规则条款  | 验证逻辑                               |
| ------------------------- | ----- | ---------------------------------- |
| `test_initial_deal`       | 规则二.1 | 每人7手牌+13牌库+12墓地=52                 |
| `test_mulligan_swap`      | 规则二.2 | 换2张后手牌仍7张，被换的牌不在手中，轮转到对方           |
| `test_mulligan_max_three` | 规则二.2 | 尝试换4张→抛IllegalActionError          |
| `test_initial_deploy`     | 规则二.3 | 部署1张到待战区→hand-1,back+1；双方完成后进入准备阶段 |


### 2. PrepareTests（准备阶段）


| 用例                            | 规则条款  | 验证逻辑                      |
| ----------------------------- | ----- | ------------------------- |
| `test_prepare_moves_to_front` | 规则四.1 | 待战区全部→作战区，进入行动阶段，首回合行动力=1 |
| `test_prepare_partial_move`   | 规则四.1 | 只移1张：front+1,back-1       |
| `test_prepare_no_move`        | 规则四.1 | 全不移：front=0,back不变        |


### 3. ActionTests（行动阶段）


| 用例                             | 规则条款    | 验证逻辑              |
| ------------------------------ | ------- | ----------------- |
| `test_recruit`                 | 规则四.2.1 | 墓地-2,牌库+2,行动力-1   |
| `test_train`                   | 规则四.2.2 | 牌库-2,手牌+2         |
| `test_no_repeat_action`        | 规则四.2   | 征兵两次→抛错           |
| `test_first_turn_one_action`   | 规则二.4   | 首回合行动力=1,用完后再用→抛错 |
| `test_second_turn_two_actions` | 规则四.2   | 走完整回合循环后验证行动力=2   |


### 4. BalanceTests（制衡操作）


| 用例                               | 规则条款        | 验证逻辑            |
| -------------------------------- | ----------- | --------------- |
| `test_balance_deck_swap`         | 规则四.2.4 模式A | 手牌[0]↔牌库[0]交换成功 |
| `test_balance_deck_max_four`     | 规则四.2.4     | 尝试交换5次→抛错       |
| `test_balance_graveyard`         | 规则四.2.4 模式B | 1张手牌↔墓地交换，手牌数不变 |
| `test_balance_graveyard_max_two` | 规则四.2.4     | 尝试换3张→抛错        |


### 5. HQPenaltyValueTests（大本营惩罚点数）


| 用例                      | 规则条款     | 验证逻辑                               |
| ----------------------- | -------- | ---------------------------------- |
| `test_penalty_values`   | 规则六.7    | A=1, 10=5, J=5, Q=5, K=5, 7=7, 2=2 |
| `test_discard_x_values` | 规则六.2(X) | 2-5→3, 6-9→2, 10JQK→1, A低→4, A高→1  |


### 6. AttackTests（攻击结算 — 基础）


| 用例                                      | 规则条款      | 验证逻辑                   |
| --------------------------------------- | --------- | ---------------------- |
| `test_clubs_first_card_doubles`         | 规则六.2+六.3 | 梅花5+梅花3→(5+3)×2=16     |
| `test_clubs_second_not_doubled`         | 规则六.3     | 红桃5+梅花3→5+3=8(不翻倍)     |
| `test_hq_defense_penalty`               | 规则六.7     | A(1)+K(5)+Q(5)=11≥10防住 |
| `test_hq_defense_defeat`                | 规则六.7     | 牌库不足→GameOverError     |
| `test_troop_destroyed_skips_discard`    | 规则五.4+六.6 | 击毁部队的红桃不触发弃牌效果         |
| `test_silenced_blocks_attacker_discard` | 规则六.2(沉默) | 首张黑桃总和≥攻击→攻击方弃牌无效      |
| `test_reorganize_adjacent_only`         | 规则四.2.3   | 区0和区2不相邻→抛错            |
| `test_spade_double_optional`            | 规则六.2(黑桃) | 选择不翻倍→4<7,部队被击毁        |


### 7. OverflowTests（溢出攻击）


| 用例                             | 规则条款      | 验证逻辑                     |
| ------------------------------ | --------- | ------------------------ |
| `test_overflow_to_hq`          | 规则五.4     | 20-4=16溢出打大本营，验证溢出报告     |
| `test_overflow_to_back`        | 规则五.4     | 20-2=18溢出打待战区K+K=20≥18防住 |
| `test_overflow_defeats_player` | 规则五.4+六.7 | 溢出耗尽大本营→GameOverError    |


### 8. RescueTests（红桃急救）


| 用例                         | 规则条款        | 验证逻辑                           |
| -------------------------- | ----------- | ------------------------------ |
| `test_rescue_adds_defense` | 规则六.2(红桃急救) | 翻红桃3→打出手牌黑桃K(20)→3+20=23≥10防住  |
| `test_rescue_limit_once`   | 规则六.2       | 两张红桃只有第一张触发急救，hand_rescue事件仅1个 |


### 9. DiscardEffectTests（弃牌效果）


| 用例                                           | 规则条款       | 验证逻辑                     |
| -------------------------------------------- | ---------- | ------------------------ |
| `test_hearts_discard_draws_from_deck`        | 规则六.2(红桃)  | 红桃5 X=3→牌库-3,手牌+3        |
| `test_diamonds_discard_draws_from_graveyard` | 规则六.2(方片)  | 方片4 X=3→墓地-3,牌库+3        |
| `test_discard_first_suit_rule_blocks`        | 规则六.3      | 第一张梅花→第二张红桃不触发弃牌         |
| `test_x_cap_at_six`                          | 规则六.2(X上限) | 红桃2+红桃3→X=3+3=6(cap),满抽6 |


### 10. ContinueDefenseTests（继续翻牌）


| 用例                       | 规则条款  | 验证逻辑                       |
| ------------------------ | ----- | -------------------------- |
| `test_continue_flipping` | 规则五.3 | 防御成功后选继续翻→defense_cards有2张 |


### 11. AttackTargetTests（攻击目标选择）


| 用例                                  | 规则条款    | 验证逻辑                            |
| ----------------------------------- | ------- | ------------------------------- |
| `test_must_attack_front_if_present` | 规则四.2.5 | 作战区有部队→target_type="front"      |
| `test_choose_hq_when_front_empty`   | 规则四.2.5 | 作战区空→选"hq"→target_type="hq"     |
| `test_choose_back_when_front_empty` | 规则四.2.5 | 作战区空→选"back"→target_type="back" |


### 12. FourElementDefenseTests（四象防御特殊规则）


| 用例                                 | 规则条款  | 验证逻辑                       |
| ---------------------------------- | ----- | -------------------------- |
| `test_four_suits_instant_defense`  | 特殊规则1 | 四花色齐→defense_held=True,不击毁 |
| `test_four_element_off_by_default` | 特殊规则1 | 默认关闭→10<20,部队被击毁           |


### 13. DeployTests（部署）


| 用例                             | 规则条款  | 验证逻辑               |
| ------------------------------ | ----- | ------------------ |
| `test_deploy_to_back`          | 规则四.3 | 牌出现在back[1],hand减少 |
| `test_deploy_exceeds_max`      | 规则六.1 | 放6张→抛错(单部队≤5)      |
| `test_deploy_card_not_in_hand` | 规则四.3 | 手中没有的牌→抛错          |


### 14. FullGameFlowTest（端到端流程）


| 用例                         | 规则条款 | 验证逻辑                  |
| -------------------------- | ---- | --------------------- |
| `test_full_turn_cycle`     | 全流程  | 走3个完整回合不崩溃,验证阶段/行动力转换 |
| `test_game_over_detection` | 规则一  | 训练抽空牌库→GameOverError  |


