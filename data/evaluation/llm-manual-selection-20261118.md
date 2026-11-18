# 全カタログからのLLMマニュアル選定: 探索的評価

実施日: 2026-11-18

## 方法と制約

この会話のAIが、Catalyst 9300 / IOS XE 26.xのカタログ全15文書・303章の
文書名と章名を読み、Qdrantで事前に絞り込まずに選定した。追加のLLM API呼び出しは行っていない。
以下の選定は本文による照合前に記録した。1項目内の候補順は表記順とする。
理由は章名と要件の対応であり、選定は本文の適合性や機器での動作を保証しない。

過去の検索評価の結果や一部の期待文書は選定者に既知であり、独立した盲検ではない。
1回の試行であるため、再現性や汎化性能は評価できない。
13件の要件はGit履歴 `6d0523b:data/evaluation/config_cases.json`、
複合要件は同 `data/evaluation/parameter-sheet-demo.csv`、
DHCPv6の手順は同 `data/corrections/c9300-stateful-dhcp/stateful-dhcp.md` を使用した。

## 候補の識別子

各章はカタログ中の文書IDと章URLに対応する。記号はこの評価に限った略称。

| 略称 | 文書ID | 章 |
|---|---|---|
| I | `catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f` | [Configuring Interface Characteristics](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html) |
| D | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d` | [Configuring DHCP](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/ip/b_26x_ip_9300_cg/configuring_dhcp.html) |
| R4 | `catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b` | [Configuring IP Unicast Routing](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/rtng/b_26x_rtng_9300_cg/configuring_ip_unicast_routing.html) |
| R6 | `catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b` | [Configuring IPv6 Unicast Routing](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/rtng/b_26x_rtng_9300_cg/configuring_ipv6_unicast_routing.html) |
| N | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52` | [Configuring Simple Network Management Protocol](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/nmgmt/b_26x_nmgmt_9300_cg/configuring_simple_network_management_protocol.html) |
| S | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f` | [Administering the Device](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sys_mgmt/b_26x_sys_mgmt_9300_cg/administering_the_device.html) |
| L | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f` | [Configuring System Message Logs](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sys_mgmt/b_26x_sys_mgmt_9300_cg/configuring_system_message_logs.html) |
| T | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc` | [Configuring VTP](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vtp.html) |
| V | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc` | [Configuring VLANs](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html) |

## 固定した選定結果

| ケース | 要件項目 → 順位付き候補 | 選定理由・不確実性 |
|---|---|---|
| routed-port-ipv4 | ルーテッドポート → I, R4; IPv4アドレス → R4, I | インターフェースの特性とIPv4ルーティング。具体的なアドレス設定箇所は未確認。 |
| interface-description | ポート説明 → I | インターフェースの一般設定。説明設定を含むか未確認。 |
| access-vlan | アクセスポート・VLAN所属 → V, I | VLANとポートの一般設定。アクセスモードを含むか未確認。 |
| dhcp-relay | IPv4 DHCPリレー → D | DHCPの一般章。通常の同一VRF向け手順が存在するか未確認。 |
| dhcp-pool | DHCPプール・ゲートウェイ → D | DHCPの一般章。サーバー設定を含むか未確認。 |
| ipv6-interface | IPv6ルーティング・SVIアドレス → R6, V | IPv6ルーティングとVLANの一般章。SVIへの適用箇所は未確認。 |
| ntp-server | 時刻同期先 → S | デバイス管理の章に時刻管理が含まれる可能性。NTP専用章がないため不確実。 |
| syslog-server | ログ送信先 → L | システムログの章。外部サーバーへの送信手順は未確認。 |
| snmp-community | SNMP読み取り専用コミュニティ → N | SNMPの章。v2cと読み取り専用の対応は未確認。 |
| interface-shutdown | ポート停止 → I | インターフェースの一般設定。停止手順の有無は未確認。 |
| minimal-interface-range | 一括インターフェース指定 → I; アクセスポート・VLAN所属 → V, I | インターフェース範囲とVLAN。短い表記の対応は未確認。 |
| vtp-transparent | VTPの既定値・transparentモード → T | VTPの章。既定値と当該リリースの適用条件は未確認。 |
| vlan-name | VLAN作成・名前 → V | VLANの一般章。名前設定を含むか未確認。 |
| parameter-sheet VLAN 120 | VLAN作成 → V; アクセスポート → V, I; SVI・IPv4 → R4, V; DHCPリレー → D | 4項目を分離。SVIは章名だけで場所が分からず不確実。routingとDHCPサービスは有効済みという入力前提であり変更を選定しない。 |
| Stateful DHCPv6 14手順 | 外部Stateful DHCPv6の一連の手順 → D | 以前の手順の題名とDHCP一般章に基づく。14手順が同じ章に揃うか、一般操作が妥当かは未確認。 |

設定生成や設定適用は行っていない。

## 照合と集計

選定後、OKFの対応章を確認し、次の限定的な正解集合を事後に定めた。
完全な正解章の網羅ではなく、各要件の検討を進めるために必要と判断した代表章である。
同じAIが選定と判定を行ったため、評価者バイアスもある。PDF原本の再照合は
この評価では行っておらず、OKFの内容確認を適用可能な根拠の検証とは区別する。

| ケース | 代表正解章 | 結果・確認できた内容 |
|---|---|---|
| routed-port-ipv4 | I | 一致。IのLayer 3インターフェース手順で物理ポートとIPv4アドレスを扱う。R4も関連。 |
| interface-description | I | 一致。Iにインターフェースの説明設定節がある。 |
| access-vlan | V | 一致。Vに静的アクセスポートのVLAN割当手順がある。Iは概要として関連。 |
| dhcp-relay | D | 一致。Dに通常のパケット転送先指定手順がある。 |
| dhcp-pool | 未確定 | DにDHCPサーバー節はあるが、要求されたプールとデフォルトゲートウェイの一連の手順を確認できなかった。代表正解を確定できず集計対象外。 |
| ipv6-interface | R6, I | R6は一致。SVIの作成・指定を明示的に扱うIは未選定。VはSVIの概要に触れるが、その手順としては数えない。 |
| ntp-server | S | 一致。SにNTPの時刻同期先設定節がある。 |
| syslog-server | L | 一致。Lに外部syslog送信先の指定手順がある。 |
| snmp-community | N | 一致。Nに読み取り専用コミュニティの例がある。 |
| interface-shutdown | I | 一致。Iにインターフェース停止の節がある。 |
| minimal-interface-range | I, V | 両方一致。Iは範囲指定、VはアクセスポートとVLANの割当を扱う。 |
| vtp-transparent | T | 一致。Tに既定モードとモード設定が記載されている。 |
| vlan-name | V | 一致。VにVLAN作成と名前設定の手順がある。 |

Recall@kは、各要件項目の上位k件をケース単位で合併し、ケースごとに
「合致した代表正解章数 / 代表正解章数」を求め、その平均を取った。
文書Recallは代表正解章を文書IDに置き換えて計算した。rankは要件項目ごとの順序であり、
従来のQdrantのチャンク順位とは異なる。

| 指標 | 結果 |
|---|---:|
| 章Recall@1 / @2（12ケースのマクロ平均） | 11.5/12 = 95.8% / 11.5/12 = 95.8% |
| 文書Recall@1 / @2（同上） | 11.5/12 = 95.8% / 11.5/12 = 95.8% |
| 代表正解章をすべて選定できたケース | 11/12 |

13件の選定候補20件を**要件項目とのトピック関連性**で確認したところ、明らかに
無関係な候補は0/20だった。ただし、一般的な概要を含むだけでも関連とし、
DHCPプールのDも関連あり・内容不足として数えた。これは有用な根拠の精度ではない。
候補をすべて有用と判断したという意味ではなく、未確認の章も正解ではない。

### 複合要件

VLAN 120の4項目では、VLAN作成、アクセスポート、DHCPリレーの3項目は
対応する一般的な手順を選定できた。SVI・IPv4はR4とVに関連する説明があるが、
SVIを指定してIPv4を設定する明示的な手順を含むIは選ばなかった。
したがって、明示的な手順を含む章の項目網羅率は**3/4**であり、
SVIは部分的な関連候補に留まる。

Stateful DHCPv6は、Dの「Configuring Stateful DHCP Externally」にStep 1〜14の
表が存在し、**14/14手順が選択章に含まれる**。これは章への到達率であり、
個々の手順を検索・抽出した結果ではない。以前の原本照合ではStep 10の説明、
Step 14の表記、Step 3〜4のモード移行に注意点があったため、14手順の存在を
完全な設定手順の妥当性と同一視しない。

### Qdrantとの比較と判断

前回の日本語要件そのままのQdrant検索では、期待する機能の章がTop 10に
6/13、Top 1に4/13だった。VLAN 120複合要件では上位20チャンクがDHCPリレーの
EVPN文脈に偏り、VLAN作成とSVIの章を発見できなかった。Stateful DHCPv6は
上位20チャンク中で14手順を取得できていた。

今回の試行では全目次によって通常のDHCPリレーやVLANの章に到達できた一方、
SVI章の漏れとDHCPプールの内容不足が残った。ただし、前回のチャンク検索と今回の
項目ごとの章選定は順位・正解集合・集計単位が異なり、数値を直接比較して
性能改善を断定できない。事後正解かつ選定者に過去の結果が既知のため、
今回のRecallも独立した性能推定ではない。

次の評価では、正解章と採点ルールを先に固定し、過去結果を見ない別の選定実行を
複数回行う。同じ項目分割、章への重複除去、k、関連性判定でQdrantと比較し、
内容不足・未確定を成功に含めない。

入力のSHA-256: カタログ
`607168b871f1ff73c652609b2b0a95bf82821e8f752f2ed7705a6976817ff288`、
13件の要件JSON
`da13b8793591f7940b9636a3eea5d30f7bc52a2865e3d32c44c1a00099032939`。
