# コンフィグ案レポート

> **評価用・実機投入不可。** 原本PDFの内容は未照合です。
> 出典・構文の検証は、設定の意味、順序、モード、許容値や手順の網羅性を保証しません。
> 状態はLLMの判定です。この表示処理自体は元資料の再検証を行いません。

対象: catalyst9300 / 26.x

## 入力要件

VLAN 120を作成し、アクセスポートに割り当てる

## 要件ごとの結果

| 項目 | 要件 | 状態 |
| --- | --- | --- |
| [item-1](#item-1) | VLAN 120 をスイッチ上に作成する | 部分対応 (partial) |
| [item-2](#item-2) | 物理アクセスポートを VLAN 120 に割り当てる | 追加情報が必要 (needs_input) |

## item-1

VLAN 120 をスイッチ上に作成する

**状態: 部分対応 (partial)**

要件は「VLAN 120 をスイッチ上に作成する」です。提供された手順から、通常レンジ VLAN の作成コマンド (vlan vlan-id) を VLAN ID=120 で具体化でき、また show vlan による確認も可能です。一方で、VLAN 名やメディア種別、VTP モード／ドメインなどの設計要素は要件に明示されておらず、かつ任意設定であるため、本ドラフトでは追加設定を行っていません。また、VTP や拡張レンジ VLAN、プライベート VLAN などの高度な構成は要件からは読み取れないため反映していません。そのため、VLAN 120 の作成と確認に関するコマンドは提示できますが、VTP 設計や VLAN の種別（Voice VLAN や PVLAN など）に関する不確実性が残るため、ステータスは partial としています。

### 設定案

```text
configure terminal
vlan 120
end
```

| CLI行 | 構文ID | 出典 |
| --- | --- | --- |
| 1 | command-1 | [source-11](#source-11) / Step 1 / 範囲内の表 1・行 2 |
| 2 | command-2 | [source-11](#source-11) / Step 2 / 範囲内の表 1・行 3 |
| 3 | command-5 | [source-11](#source-11) / Step 5 / 範囲内の表 1・行 6 |

### 設定テンプレート（未指定値の解決が必要）

なし。

### 確認コマンド

```text
show vlan id 120
```

| CLI行 | 構文ID | 出典 |
| --- | --- | --- |
| 1 | command-7 | [source-11](#source-11) / Step 7 / 範囲内の表 1・行 8 |

### 確認テンプレート（未指定値の解決が必要）

なし。

### 不足情報

- VLAN 120 を Voice VLAN や Private VLAN として扱う必要があるかどうか（要件では通常のデータ VLAN かどうかが明示されていない）
- VLAN 120 に付与すべき VLAN 名（name コマンドで設定する名称）が指定されていない
- スイッチの VTP モード（server / transparent / client / off）および VTP ドメイン名の設計方針
- VLAN 120 をどのスイッチ群で共有するか（単一スイッチローカルか、VTP でドメイン全体に配布するか）

### 前提・仮定

- VLAN 120 は通常のイーサネット VLAN（標準データ VLAN）として作成し、Voice VLAN や Private VLAN などの特殊な属性は不要であると仮定している。
- VLAN 120 の作成のみが本アイテムの対象であり、SVI 作成や IP アドレス付与、ルーティング設定は別要件で扱われるとみなしている。
- デバイスは既に VTP サーバーまたはトランスペアレントモードで動作しており、この前提を満たしていると仮定している。

### 注意事項・制約

VLAN 作成には、デバイスが VTP サーバーモードまたは VTP トランスペアレントモードである必要があります。VTP クライアントモードでは VLAN をローカルに作成できません。

- [source-7](#source-7)
  > Before you can create a VLAN, the device must be in VTP server mode or VTP transparent mode.

VLAN ID は 1〜4094 の範囲であり、120 はこの範囲内の通常レンジ VLAN として扱えます。

- [source-11](#source-11)
  > Note The available VLAN ID range for this command is 1 to 4094.

- [source-5](#source-5)
  > VLANs are identified by a number from 1 to 4094.

### 根拠の説明

通常レンジ VLAN（1〜4094）の作成手順として、グローバルコンフィグモードに入り、vlan vlan-id で VLAN を作成し、show vlan で確認する手順が示されています。これを VLAN ID 120 に適用できます。

- [source-11](#source-11)
  > Step 1 configure terminal Example: Device\# configure terminal Enters global configuration mode.

- [source-11](#source-11)
  > Step 2 vlan vlan-id Example: Device(config)\# vlan 20 Enters a VLAN ID, and enters VLAN configuration mode. Enter a new VLAN ID to create a VLAN, or enter an existing VLAN ID to modify that VLAN. Note The available VLAN ID range for this command is 1 to 4094.

- [source-11](#source-11)
  > Step 7 show vlan { name vlan-name \| id vlan-id } Example: Device\# show vlan name test20 or Device\# show vlan id 20 Verifies your entries.

- [source-5](#source-5)
  > VLANs are identified by a number from 1 to 4094.

VLAN 作成前に VTP をどう扱うか検討すべきという前提条件が示されていますが、本要件では VTP の利用有無やドメイン設計が指定されていません。

- [source-1](#source-1)
  > Before you create VLANs, you must decide whether to use VLAN Trunking Protocol (VTP) to maintain global VLAN configuration for your network.

- [source-7](#source-7)
  > Before you can create a VLAN, the device must be in VTP server mode or VTP transparent mode.

### 採用を保留した手順

```text
name vlan-name
```

VLAN 名 (vlan-name) が要件で指定されておらず、任意設定であるため、本ドラフトでは具体値を推測せずに省略した。

- [source-11](#source-11) / Step 3 / 範囲内の表 1・行 4

```text
media { ethernet | fd-net | fddi | tokenring | trn-net }
```

media の指定は任意であり、要件にメディア種別の指定がないため、省略した。

- [source-11](#source-11) / Step 4 / 範囲内の表 1・行 5

```text
copy running-config startup-config
```

copy running-config startup-config は任意の保存操作であり、要件に明示されていないため、本ドラフトでは自動的に永続化を行わない方針で省略した。

- [source-12](#source-12) / Step 9 / 範囲内の表 1・行 10

```text
copy running-config startup-config
```

同様に、VLAN 作成後の設定保存は任意であり、要件に含まれていないため省略した。

- [source-14](#source-14) / Step 8 / 範囲内の表 1・行 9

```text
copy running-config startup-config
```

VTP 関連の設定保存であり、本アイテムの範囲（VLAN 120 作成）の外であるため省略した。

- [source-27](#source-27) / Step 7 / 範囲内の表 1・行 8

```text
copy running-config startup-config
```

VTP バージョン設定後の保存であり、VLAN 120 作成要件には含まれないため省略した。

- [source-29](#source-29) / Step 6 / 範囲内の表 3・行 7

### 推論候補（根拠付き設定とは別・未検証）

VLAN 120 に対して name コマンドで論理名（例: DATA\_VLAN\_120 など）を設定すること。

- 必要な前提: 運用ポリシーとして、全 VLAN に一貫した命名規則を適用している。

VTP を使用しない設計とし、スイッチを VTP transparent モードにしてローカルに VLAN 120 を管理すること。

- 必要な前提: ネットワーク規模が比較的小さく、VTP による集中管理が必須ではない。
- 必要な前提: 既存環境が VTP transparent ベースで構成されている。

VLAN 120 をアクセスポートに割り当てるため、対象インターフェイスで switchport mode access および switchport access vlan 120 を設定すること。

- 必要な前提: 別アイテムまたは別要件で、どの物理ポートを VLAN 120 に割り当てるかが指定される。

## item-2

物理アクセスポートを VLAN 120 に割り当てる

**状態: 追加情報が必要 (needs_input)**

要求は「物理アクセスポートを VLAN 120 に割り当てる」だが、どの物理インターフェースを対象とするかが不明であり、既存設定（トランク／Voice VLAN／ポートセキュリティ／QoS など）との関係も不明である。ソースでは、静的アクセスポートを VLAN に割り当てる手順として、対象インターフェースを指定し switchport mode access と switchport access vlan vlan-id を設定することが示されているが、具体的な interface-id は要求から得られないため、テンプレートとしてプレースホルダを用いた構成案のみ提示できる。実際の設定適用には、対象インターフェースの特定と、既存のトランク／Voice VLAN／Layer 3 モードなどとの整合性確認が必要である。

### 設定案

なし。

### 設定テンプレート（未指定値の解決が必要）

```text
enable
configure terminal
interface <INTERFACE_ID>
switchport mode access
switchport access vlan 120
end
```

| CLI行 | 構文ID | 出典 |
| --- | --- | --- |
| 1 | command-8 | [source-12](#source-12) / Step 1 / 範囲内の表 1・行 2 |
| 2 | command-9 | [source-12](#source-12) / Step 2 / 範囲内の表 1・行 3 |
| 3 | command-10 | [source-12](#source-12) / Step 3 / 範囲内の表 1・行 4 |
| 4 | command-11 | [source-12](#source-12) / Step 4 / 範囲内の表 1・行 5 |
| 5 | command-12 | [source-12](#source-12) / Step 5 / 範囲内の表 1・行 6 |
| 6 | command-13 | [source-12](#source-12) / Step 6 / 範囲内の表 1・行 7 |

### 確認コマンド

なし。

### 確認テンプレート（未指定値の解決が必要）

```text
show running-config interface <INTERFACE_ID>
show interfaces <INTERFACE_ID> switchport
show vlan id 120
```

| CLI行 | 構文ID | 出典 |
| --- | --- | --- |
| 1 | command-14 | [source-12](#source-12) / Step 7 / 範囲内の表 1・行 8 |
| 2 | command-15 | [source-12](#source-12) / Step 8 / 範囲内の表 1・行 9 |
| 3 | command-66 | [source-38](#source-38) / show vlan \[ access-map name \| brief \| dot1q { tag native } \| filter \[ access-map \| vlan \] \| group \[ group-name name \] \| id vlan-id \| ifindex \| mtu \| name name \| private-vlan remote-span \| summary \] / 範囲内の表 1・行 3 |

### 不足情報

- VLAN 120 を割り当てる対象の物理インターフェース ID（例: GigabitEthernet1/0/1）が不明である。
- 対象インターフェースが現在アクセスポートなのかトランクポートなのか、あるいは Layer 3 インターフェースなのかが不明である。
- 対象インターフェースに既存の Voice VLAN 設定（switchport voice vlan）や QoS 設定、ポートセキュリティ設定があるかどうかが不明である。
- VLAN 120 が既にデバイス上に存在しているか、事前に作成済みかどうかが不明である。

### 前提・仮定

なし。

### 注意事項・制約

対象インターフェースが既に Layer 3 インターフェース（no switchport）として使用されている場合、switchport mode access を設定すると Layer 2 モードへ戻り、既存の Layer 3 設定が失われる可能性があるため、事前確認が必要である。

- [source-69](#source-69)
  > Routed ports are physical ports configured to be in Layer 3 mode by using the no switchport interface configuration command.

- [source-66](#source-66)
  > To configure Layer 2 parameters, if the interface is in Layer 3 mode, you must enter the switchport interface configuration command without any parameters to put the interface into Layer 2 mode. This shuts down the interface and then re-enables it, which might generate messages on the device to which the interface is connected. When you put an interface that is in Layer 3 mode into Layer 2 mode, the previous configuration information related to the affected interface might be lost, and the interface is returned to its default configuration.

インターフェースの速度やデュプレックスを変更する場合、再設定時にインターフェースがシャットダウン／再有効化される可能性があり、STP の再収束などで一時的な通信断が発生しうる。

- [source-67](#source-67)
  > Caution Changing the interface speed and duplex mode configuration might shut down and re-enable the interface during the reconfiguration.

### 根拠の説明

静的アクセスポートを VLAN に割り当てる手順として、enable で特権 EXEC モードに入り、configure terminal でグローバルコンフィグモードに移行し、interface interface-id で対象インターフェースを指定した後、switchport mode access で VLAN メンバーシップモードをアクセスに設定し、switchport access vlan vlan-id でポートを指定 VLAN に割り当てることが示されている。

- [source-12](#source-12)
  > Assigning Static-Access Ports to a VLAN You can assign a static-access port to a VLAN without having VTP globally propagate VLAN configuration information by disabling VTP (VTP transparent mode). If you assign an interface to a VLAN that does not exist, the new VLAN is created. In case of interface templates, make sure to create the VLAN explicitly before applying the command via templates. Procedure Command or Action Purpose Step 1 enable Example: Device&gt; enable Enables privileged EXEC mode. Enter your password if prompted. Step 2 configure terminal Example: Device\# configure terminal Enters global configuration mode Step 3 interface interface-id Example: Device(config)\# interface gigabitethernet2/0/1 Enters the interface to be added to the VLAN. Step 4 switchport mode access Example: Device(config-if)\# switchport mode access Defines the VLAN membership mode for the port (Layer 2 access port). Step 5 switchport access vlan vlan-id Example: Device(config-if)\# switchport access vlan 2 Assigns the port to a VLAN. Valid VLAN IDs are 1 to 4094.

VLAN ポートメンバーシップモードとして、静的アクセスポートは 1 つの VLAN に属し、手動でその VLAN に割り当てられることが示されており、要求の「物理アクセスポートを VLAN 120 に割り当てる」動作に合致する。

- [source-37](#source-37)
  > Static-access A static-access port can belong to one VLAN and is manually assigned to that VLAN.

ポートベース VLAN の説明では、ポートを VLAN に追加するには interface コンフィグモードで switchport コマンドを用い、アクセスポートの場合は所属する VLAN を定義することが示されている。

- [source-57](#source-57)
  > Add ports to a VLAN by using the switchport command in interface configuration mode. Identify the interface. For a trunk port, set trunk characteristics, and, if desired, define the VLANs to which it can belong. For an access port, set and define the VLAN to which it belongs.

アクセスポートは 1 つの VLAN のトラフィックのみを運び、Cisco IP Phone が接続される場合には、音声用 VLAN とデータ用 VLAN を分けて設定できることが示されている。既存の Voice VLAN 設定がある場合、アクセス VLAN を変更するとデータ側の VLAN が変わるため、事前確認が必要となる。

- [source-59](#source-59)
  > Access Ports An access port belongs to and carries the traffic of only one VLAN (unless it is configured as a voice VLAN port).

- [source-59](#source-59)
  > You can also configure an access port with an attached Cisco IP Phone to use one VLAN for voice traffic and another VLAN for data traffic from a device attached to the phone.

Voice VLAN の前提条件として、Voice VLAN はアクセスポート上でのみサポートされ、トランクポートではサポートされないことが示されている。対象ポートが既にトランクとして使用されている場合、Voice VLAN 設定との整合性を考慮する必要がある。

- [source-39](#source-39)
  > Voice VLAN configuration is only supported on device access ports; voice VLAN configuration is not supported on trunk ports.

インターフェースコンフィグモードへの入り方として、interface interface-id で物理インターフェースを指定する方法が説明されているが、具体的な interface-id は機器構成に依存し、要求からは決定できない。

- [source-64](#source-64)
  > To configure a physical interface (port), specify the interface type, stack member number (only stacking-capable switches), module number, and device port number, and enter interface configuration mode.

### 採用を保留した手順

```text
copy running-config startup-config
```

running-config を startup-config に保存する copy running-config startup-config はソース上「(Optional)」とされており、要求に明示されていないため自動的に含めていない。

- [source-12](#source-12) / Step 9 / 範囲内の表 1・行 10

```text
show interfaces [ vlan vlan-id ]
```

show interfaces \[ vlan vlan-id \] による VLAN インターフェース特性の確認は有用だが、要求は物理アクセスポートの割り当てに限定されており、必須検証ステップとしては明示されていないためテンプレートには含めていない。

- [source-38](#source-38) / show interfaces \[ vlan vlan-id \] / 範囲内の表 1・行 2

### 推論候補（根拠付き設定とは別・未検証）

対象インターフェースとして、典型的なユーザ接続ポート（例: GigabitEthernet1/0/1）を選定し、VLAN 120 をデータ VLAN として割り当てる運用設計。

- 必要な前提: GigabitEthernet1/0/1 がユーザ端末接続用のアクセスポートとして空いている。
- 必要な前提: 当該ポートに既存のトランク設定や Layer 3 設定が存在しない。

VLAN 120 がまだ存在しない場合、事前に vlan 120 を作成し media ethernet を設定する構成を追加する。

- 必要な前提: VLAN 120 は未作成であり、新規に作成しても他機器との整合性に問題がない。

## 次に確認すること

- スイッチの現在の VTP モードと VTP ドメイン名を show vtp status などで確認し、VLAN 作成前提（server または transparent モード）を満たしているかを確認する。
- VLAN 120 をどの用途（データ／音声／管理／PVLAN など）で利用するかを設計側に確認し、必要であれば追加属性（Voice VLAN 設定や PVLAN 設定など）を別要件として定義する。
- VLAN 120 を割り当てる対象アクセスポート（インターフェイス ID）の一覧を確定し、別アイテムとして switchport mode access / switchport access vlan 120 の設定テンプレートを作成する。
- 運用ポリシーに基づき、VLAN 名の命名規則を確認し、VLAN 120 に付与すべき name を決定する。
- 対象とする物理インターフェース ID（例: GigabitEthernet&lt;STACK&gt;/&lt;MODULE&gt;/&lt;PORT&gt;）を特定する。
- show running-config interface &lt;INTERFACE\_ID&gt; および show interfaces &lt;INTERFACE\_ID&gt; switchport を用いて、対象インターフェースが既にトランク／Layer 3／Voice VLAN ポートとして使用されていないか確認する。
- show vlan id 120 で VLAN 120 の存在を確認し、存在しない場合は別アイテムの手順に従って VLAN 120 を作成する。
- Voice VLAN やポートセキュリティ、QoS の要件がある場合、それらの設定と VLAN 120 へのアクセス VLAN 割り当ての整合性を設計する。

## 制限事項

- Chapter caution headings identify review candidates, not proven applicability or completeness.
- Cross-references outside the retrieved sections are not followed automatically.
- PDF hashes are checked, but PDF content and exact procedure pages are not verified.
- Syntax and citation checks do not prove semantic applicability, command order or mode correctness.
- Only commands from procedure-table command columns are eligible for grounded CLI output.

## 応答修正履歴

修正依頼なし。

## 参照資料

### source-11

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / How to Configure VLANs / How to Configure Normal-Range VLANs / Creating or Modifying an Ethernet VLAN
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 304–610
- OKF行: 2768–3074
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-7

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / Information About VLANs / Normal-Range VLAN Configuration Guidelines
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 226–254
- OKF行: 2690–2718
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-5

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / Information About VLANs / Supported VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 88–107
- OKF行: 2552–2571
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-1

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / Prerequisites for VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 5–16
- OKF行: 2469–2480
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-12

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / How to Configure VLANs / How to Configure Normal-Range VLANs / Assigning Static-Access Ports to a VLAN
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 791–1027
- OKF行: 3255–3491
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-14

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / How to Configure VLANs / How to Configure Extended-Range VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 1028–1247
- OKF行: 3492–3711
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-27

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring VTP
- 節: Configuring VTP / How to Configure VTP / Configuring a VTP Version 3 Password
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vtp.html)
- 章内行: 721–952
- OKF行: 1103–1334
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 13（手順のページではありません）

### source-29

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring VTP
- 節: Configuring VTP / How to Configure VTP / Enabling the VTP Version
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vtp.html)
- 章内行: 1032–1226
- OKF行: 1414–1608
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 13（手順のページではありません）

### source-38

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / Monitoring VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 1248–1354
- OKF行: 3712–3818
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-69

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Layer 3 Interfaces
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 1003–1078
- OKF行: 1383–1458
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）

### source-66

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Default Ethernet Interface Configuration
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 600–791
- OKF行: 980–1171
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）

### source-67

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Speed and Duplex Configuration Guidelines
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 819–875
- OKF行: 1199–1255
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）

### source-37

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 3 / Configuring VLANs
- 節: Configuring VLANs / Information About VLANs / VLAN Port Membership Modes
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_vlans.html)
- 章内行: 108–186
- OKF行: 2572–2650
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 35（手順のページではありません）

### source-57

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Interface Types / Port-Based VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 32–58
- OKF行: 412–438
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）

### source-59

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Interface Types / Switch Ports / Access Ports
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 69–82
- OKF行: 449–462
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）

### source-39

- 文書: VLAN Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 4 / Configuring Voice VLANs
- 節: Configuring Voice VLANs / Prerequisites for Voice VLANs
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg/configuring_voice_vlans.html)
- 章内行: 5–29
- OKF行: 3994–4018
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md
- OKF SHA-256: de26413f42b2498a82b679cb3dd95cdf10bec17c0c086df862987054b96b71a9
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/b\_26x\_vlan\_9300\_cg.pdf
- PDF SHA-256: 47bc77c74d68871e1941b3d7f8bd94ef46453c30d0f5a4443a68ce03022f47d4
- PDF位置: 章先頭の物理ページ 51（手順のページではありません）

### source-64

- 文書: Interface and Hardware Components Configuration Guide, Cisco IOS XE 26.x.x (Catalyst 9300 Switches)
- 製品・リリース: catalyst9300 / 26.x
- 章: 2 / Configuring Interface Characteristics
- 節: Configuring Interface Characteristics / Information About Interface Characteristics / Interface Configuration Mode
- HTML: [章本文](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg/configuring_interface_characteristics.html)
- 章内行: 477–550
- OKF行: 857–930
- OKF: /home/iida/git/configbrain/data/okf/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/manual.md
- OKF SHA-256: 4d299e24ce4519343ef11974e2157eb067f9a3d26d00796fe5ce767cd848eead
- PDF: /home/iida/git/configbrain/data/source\_manuals/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f/b\_26x\_int\_and\_hw\_9300\_cg.pdf
- PDF SHA-256: ec7c3e347a34bb1fab34723292d14d033555dad48dd9f5fcbe0040b509ad0038
- PDF位置: 章先頭の物理ページ 17（手順のページではありません）
