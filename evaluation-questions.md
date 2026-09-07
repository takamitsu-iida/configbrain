# Phase 0 評価質問セット

## 評価条件

- 対象機種: Cisco Catalyst 9300
- 対象OS: Cisco IOS XE 26.x
- 質問数: 15件
- 評価対象: VLAN、インターフェース、System Management
- 正解判定: 期待資料・節に対応するチャンクがRecall@5で取得され、期待する設定要素を含むこと
- ページ番号: PDFの版によって変わるため、取り込み後に実測値を記録する

## VLAN設定

| ID | 代表質問 | 期待資料 | 期待する節・設定要素 | 危険な誤り |
| --- | --- | --- | --- | --- |
| VLAN-001 | Catalyst 9300でVLAN 100を作成し、名前を`USERS`に設定する方法は？ | VLAN Configuration Guide | VLANの作成、VLAN ID、VLAN名 | VLAN IDやVLAN名を別の値で生成する |
| VLAN-002 | Catalyst 9300のアクセスポートをVLAN 100に割り当てる設定は？ | VLAN Configuration Guide、Interface and Hardware Components Configuration Guide | アクセスポート、access VLAN、インターフェースモード | trunk設定を提示する、意図しないVLANを指定する |
| VLAN-003 | Catalyst 9300のトランクポートで許可するVLANを100と200に制限するには？ | VLAN Configuration Guide、Interface and Hardware Components Configuration Guide | trunk、allowed VLAN、VLANリスト | 許可VLANを無制限にする、VLAN 1を誤って追加する |
| VLAN-004 | Catalyst 9300でVLAN 100をshutdown状態にする設定と、再度有効化する設定は？ | VLAN Configuration Guide | VLAN状態、shutdown、no shutdown | インターフェースshutdownとVLAN shutdownを混同する |
| VLAN-005 | Catalyst 9300で音声用VLAN 200をアクセスポートに設定する方法は？ | VLAN Configuration Guide、Interface and Hardware Components Configuration Guide | voice VLAN、アクセスポート、VLAN 200 | data VLANとvoice VLANを混同する、QoS設定を根拠なく追加する |

## インターフェース設定

| ID | 代表質問 | 期待資料 | 期待する節・設定要素 | 危険な誤り |
| --- | --- | --- | --- | --- |
| INT-001 | Catalyst 9300のGigabitEthernet1/0/1を有効化し、説明文を設定するには？ | Interface and Hardware Components Configuration Guide | インターフェース選択、description、no shutdown | 別のインターフェースを変更する、shutdownを生成する |
| INT-002 | Catalyst 9300のGigabitEthernet1/0/1をアクセスポートとしてVLAN 100に設定するには？ | Interface and Hardware Components Configuration Guide、VLAN Configuration Guide | switchport mode access、switchport access vlan | routed port設定を提示する、VLAN 100の存在確認を省略する |
| INT-003 | Catalyst 9300のGigabitEthernet1/0/24をレイヤ3ポートにしてIPv4アドレスを設定するには？ | Interface and Hardware Components Configuration Guide | no switchport、ip address、no shutdown | L2アクセスポートのままIPを設定する、サブネットを変更する |
| INT-004 | Catalyst 9300で複数インターフェースに同じ設定を適用するinterface rangeの使い方は？ | Interface and Hardware Components Configuration Guide | interface range、対象範囲、設定の適用単位 | 対象範囲を広げすぎる、意図しないポートを含める |
| INT-005 | Catalyst 9300でインターフェースの現在の状態と設定を確認するコマンドは？ | Interface and Hardware Components Configuration Guide | show interfaces、show running-config interface、状態確認 | 変更コマンドを確認コマンドとして提示する |

## System Management設定

| ID | 代表質問 | 期待資料 | 期待する節・設定要素 | 危険な誤り |
| --- | --- | --- | --- | --- |
| SYS-001 | Catalyst 9300のホスト名を`C9300-01`に設定する方法は？ | System Management Configuration Guide | hostname、グローバル設定モード | 別の設定階層でhostnameを実行する |
| SYS-002 | Catalyst 9300でNTPサーバー`192.0.2.10`を設定し、時刻同期状態を確認するには？ | System Management Configuration Guide | NTP server、NTP状態確認、show ntp statusまたは相当コマンド | 未確認のNTP設定オプションを断定する、時刻同期を確認しない |
| SYS-003 | Catalyst 9300で管理用のIPv4デフォルトゲートウェイを設定する方法は？ | System Management Configuration Guide | 管理インターフェースまたは管理VRF、デフォルトルート/ゲートウェイ | データプレーンの経路設定と管理経路を混同する |
| SYS-004 | Catalyst 9300でSyslogサーバー`192.0.2.20`へログを送信する設定と確認方法は？ | System Management Configuration Guide | logging host、ログレベルまたは送信条件、show logging | 送信先やログレベルを根拠なく変更する |
| SYS-005 | Catalyst 9300で設定変更を保存し、保存済み設定と稼働中設定の差分を確認するには？ | System Management Configuration Guide | copy running-config startup-config、show running-config、show startup-config | 保存前に再起動を促す、runningとstartupを逆に説明する |

## 評価時の共通ルール

1. 機種またはIOS XEバージョンが一致しない資料を、正しい根拠として扱わない。
2. 期待資料・節が上位5件にない場合は、その質問を検索失敗とする。
3. コマンドの細部が資料から確認できない場合は、推測で補完せず未確定として扱う。
4. 危険な誤りに該当する回答は、検索結果が取得できていても品質不合格とする。
