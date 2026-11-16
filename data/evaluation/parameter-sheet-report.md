# Cisco RAG生成レポート

## 質問

VLAN 120を新規作成し、GigabitEthernet1/0/20をVLAN 120のアクセスポートにしてください。SVI Vlan120のIPアドレスは192.0.2.1/24とし、SVIを有効化してください。Vlan120のDHCP要求を198.51.100.50へ転送してください。ip routingは既に有効なので設定を追加しないでください。service dhcpは既に有効なので設定を追加しないでください。SVIとDHCPサーバーはデフォルトVRFを使用してください。SVIにVRF割り当ては設定しないでください。指定されていないオプションは設定しないでください。説明には各設定CLIについて「CLI: 設定CLI行 [根拠ID]」の形式を1行ずつ記載してください。各行には対応する根拠だけを記載してください。

## 生成コンフィグ

```text

```

## 説明

### VLAN

VLAN 120は通常レンジVLANとして、グローバルコンフィグモードからvlanコマンドで新規作成できます。[catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0075]にconfigure terminalでグローバルコンフィグモードへ入り、続いてvlan vlan-idでVLANコンフィグモードへ入る手順が示されています。

VLANコンフィグモードからは[catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0083]の手順に従いexitでグローバルコンフィグモードへ戻ることができます。

最後のexitは、[c9300_iosxe26_sys_mgmt_cg:section-0552][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0583][catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0052]などで示されるように、コンフィグモードから抜けてEXECモードへ戻る用途として使用できます。

### アクセスポート

要件に従い、GigabitEthernet1/0/20 を VLAN 120 のアクセスポートとして設定するために、インターフェースモードへの遷移（interface）、アクセスポート化（switchport mode access）、VLAN 120 への割り当て（switchport access vlan 120）を行い、最後に exit でインターフェース設定モードを終了しています。これらのコマンドは、静的アクセスポートを VLAN に割り当てる手順として示されている根拠 [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0081] に基づいています。exit コマンドについては、ユーザ EXEC モードへの退出コマンドとして説明されている根拠 [c9300_iosxe26_sys_mgmt_cg:section-0552] を対応根拠として使用しています。

### SVI

SVI Vlan120に対してIPアドレスを手動設定するため、根拠のSVI設定手順に従いinterface vlanコマンドでSVIのインターフェースコンフィグモードに入っています。[catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160][c9300_iosxe26_sys_mgmt_cg:section-0134][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0159][catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269]

SVIへのIPアドレス設定は、ip address ip-address subnet-mask形式で行うことが根拠に示されているため、192.0.2.1/24を192.0.2.1 255.255.255.0として設定しています。[catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160][c9300_iosxe26_sys_mgmt_cg:section-0134][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0159][catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269]

SVIを有効化するには、[ no ] shutdownがSVI有効化のためのコマンドとして示されているため、no shutdownを使用しています。[catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269]

インターフェース設定モードを終了してグローバルコンフィグモードへ戻るため、exitコマンドを使用しています。[catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160][c9300_iosxe26_sys_mgmt_cg:section-0134][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162][catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0158][catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269]

以下に各CLI行と根拠IDの対応を示します。
CLI: interface vlan 120 [E1,E5,E6,E19,E20]
CLI: ip address 192.0.2.1 255.255.255.0 [E1,E5,E6,E19,E20]
CLI: no shutdown [catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269]
CLI: exit [E1,E5,E6,E10,E20]

### DHCPリレー

SVI Vlan120上でDHCPブロードキャストを198.51.100.50へ中継するため、インターフェースコンフィグモードでip helper-addressを設定しています。[catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b:section-0172]の手順では、Layer3インターフェースに対してip helper-address addressを設定することでBOOTP/DHCPを含むUDPブロードキャストを指定アドレスへ転送することが示されています。

SVIへのアクセス方法としては[catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0126]の例でinterface vlan <vlan-id>コマンドと、その後のexitによるインターフェースモード終了が示されているため、Vlan120に対して同様の構文で設定し、最後にexitでグローバルコンフィグモードへ戻しています。

## 注意事項

- VLAN: 設定CLIと対応付いていない引用は文脈未確認です: catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0075, catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0083, c9300_iosxe26_sys_mgmt_cg:section-0552, catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0583, catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0052
- VLAN: VLAN 120の追加設定（名前付与など）は要件に含まれていないため、一切実施していません。
- VLAN: 運用確認用のshowコマンドは、根拠に記載がありますが要件外のためconfigurationには含めていません。
- VLAN: 要件にない操作・任意設定を除外しました: configure terminal
- VLAN: 未指定または対象外の設定 (global): exit
- VLAN: 引用の未確認または文脈不一致: vlan 120, exit, exit
- アクセスポート: 設定CLIと対応付いていない引用は文脈未確認です: catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0081, c9300_iosxe26_sys_mgmt_cg:section-0552
- アクセスポート: 未指定または対象外の設定 (global): CLI: interface GigabitEthernet1/0/20 [E1]
- アクセスポート: 未指定または対象外の設定 (global): CLI: switchport mode access [E1]
- アクセスポート: 未指定または対象外の設定 (global): CLI: switchport access vlan 120 [E1]
- アクセスポート: 未指定または対象外の設定 (global): CLI: exit [E3]
- アクセスポート: 必要な設定がありません (global): interface gigabitethernet1/0/20
- アクセスポート: 必要な設定がありません (interface gigabitethernet1/0/20): exit
- アクセスポート: 必要な設定がありません (interface gigabitethernet1/0/20): switchport access vlan 120
- アクセスポート: 必要な設定がありません (interface gigabitethernet1/0/20): switchport mode access
- アクセスポート: 引用の未確認または文脈不一致: CLI: interface GigabitEthernet1/0/20 [E1], CLI: switchport mode access [E1], CLI: switchport access vlan 120 [E1], CLI: exit [E3]
- SVI: 設定CLIと対応付いていない引用は文脈未確認です: catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160, c9300_iosxe26_sys_mgmt_cg:section-0134, catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162, catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0158, catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0159
- SVI: 未指定または対象外の設定 (global): interface vlan 120
- SVI: 未指定または対象外の設定 (interface vlan 120): ip address 192.0.2.1 255.255.255.0
- SVI: 未指定または対象外の設定 (interface vlan 120): no shutdown
- SVI: 未指定または対象外の設定 (interface vlan 120): exit
- SVI: 必要な設定がありません (global): interface vlan120
- SVI: 必要な設定がありません (interface vlan120): exit
- SVI: 必要な設定がありません (interface vlan120): ip address 192.0.2.1 255.255.255.0
- SVI: 必要な設定がありません (interface vlan120): no shutdown
- SVI: 引用の未確認または文脈不一致: interface vlan 120, ip address 192.0.2.1 255.255.255.0, no shutdown, exit
- DHCPリレー: 設定CLIと対応付いていない引用は文脈未確認です: catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0126, catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b:section-0172
- DHCPリレー: 未指定または対象外の設定 (global): CLI: interface vlan 120 [E3]
- DHCPリレー: 未指定または対象外の設定 (global): CLI: ip helper-address 198.51.100.50 [E17]
- DHCPリレー: 未指定または対象外の設定 (global): CLI: exit [E3]
- DHCPリレー: 必要な設定がありません (global): interface vlan120
- DHCPリレー: 必要な設定がありません (interface vlan120): exit
- DHCPリレー: 必要な設定がありません (interface vlan120): ip helper-address 198.51.100.50
- DHCPリレー: 引用の未確認または文脈不一致: CLI: interface vlan 120 [E3], CLI: ip helper-address 198.51.100.50 [E17], CLI: exit [E3]
- 要件ごとの確認または結合後の検証に失敗したため、設定全体を棄権しました。
- 索引に手順見出しの改善が未反映です。READMEの手順で対象製品を再索引化してください。

## 要件ごとの結果

候補は個別確認用であり、全体検証に失敗した場合は投入できません。

| 要件 | 状態 | 注意事項 |
|---|---|---|
| VLAN | 未確認 | VLAN 120の追加設定（名前付与など）は要件に含まれていないため、一切実施していません。; 運用確認用のshowコマンドは、根拠に記載がありますが要件外のためconfigurationには含めていません。; 要件にない操作・任意設定を除外しました: configure terminal; 未指定または対象外の設定 (global): exit; 引用の未確認または文脈不一致: vlan 120, exit, exit |
| アクセスポート | 未確認 | 未指定または対象外の設定 (global): CLI: interface GigabitEthernet1/0/20 [E1]; 未指定または対象外の設定 (global): CLI: switchport mode access [E1]; 未指定または対象外の設定 (global): CLI: switchport access vlan 120 [E1]; 未指定または対象外の設定 (global): CLI: exit [E3]; 必要な設定がありません (global): interface gigabitethernet1/0/20; 必要な設定がありません (interface gigabitethernet1/0/20): exit; 必要な設定がありません (interface gigabitethernet1/0/20): switchport access vlan 120; 必要な設定がありません (interface gigabitethernet1/0/20): switchport mode access; 引用の未確認または文脈不一致: CLI: interface GigabitEthernet1/0/20 [E1], CLI: switchport mode access [E1], CLI: switchport access vlan 120 [E1], CLI: exit [E3] |
| SVI | 未確認 | 未指定または対象外の設定 (global): interface vlan 120; 未指定または対象外の設定 (interface vlan 120): ip address 192.0.2.1 255.255.255.0; 未指定または対象外の設定 (interface vlan 120): no shutdown; 未指定または対象外の設定 (interface vlan 120): exit; 必要な設定がありません (global): interface vlan120; 必要な設定がありません (interface vlan120): exit; 必要な設定がありません (interface vlan120): ip address 192.0.2.1 255.255.255.0; 必要な設定がありません (interface vlan120): no shutdown; 引用の未確認または文脈不一致: interface vlan 120, ip address 192.0.2.1 255.255.255.0, no shutdown, exit |
| DHCPリレー | 未確認 | 未指定または対象外の設定 (global): CLI: interface vlan 120 [E3]; 未指定または対象外の設定 (global): CLI: ip helper-address 198.51.100.50 [E17]; 未指定または対象外の設定 (global): CLI: exit [E3]; 必要な設定がありません (global): interface vlan120; 必要な設定がありません (interface vlan120): exit; 必要な設定がありません (interface vlan120): ip helper-address 198.51.100.50; 引用の未確認または文脈不一致: CLI: interface vlan 120 [E3], CLI: ip helper-address 198.51.100.50 [E17], CLI: exit [E3] |

### VLANの個別候補（投入不可）

```text
vlan 120
exit
exit
```

### アクセスポートの個別候補（投入不可）

```text
CLI: interface GigabitEthernet1/0/20 [E1]
CLI: switchport mode access [E1]
CLI: switchport access vlan 120 [E1]
CLI: exit [E3]
```

### SVIの個別候補（投入不可）

```text
interface vlan 120
ip address 192.0.2.1 255.255.255.0
no shutdown
exit
```

### DHCPリレーの個別候補（投入不可）

```text
CLI: interface vlan 120 [E3]
CLI: ip helper-address 198.51.100.50 [E17]
CLI: exit [E3]
```

## 不足値

なし

## 未対応要件

- パラメータシートと生成設定の不一致
- CLIごとの引用を確認できません

## 参照根拠

- [`catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0075`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md)
- [`catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0083`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md)
- [`c9300_iosxe26_sys_mgmt_cg:section-0552`](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md)
- [`catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0583`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md)
- [`catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0052`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea/manual.md)
- [`catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0081`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md)
- [`catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md)
- [`c9300_iosxe26_sys_mgmt_cg:section-0134`](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md)
- [`catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md)
- [`catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0158`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md)
- [`catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0159`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md)
- [`catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea/manual.md)
- [`catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0126`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md)
- [`catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b:section-0172`](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b/manual.md)

## 根拠外の推測

- 知識利用ポリシー: `disallow`
- 推測コマンド数: 0

## CLIごとの引用確認

| CLI | 確認結果 | 一致した引用 | 確認できなかった引用 |
|---|---|---|---|
| `vlan 120` | 未確認 | なし | なし |
| `exit` | 未確認 | なし | なし |
| `exit` | 未確認 | なし | なし |
| `CLI: interface GigabitEthernet1/0/20 [E1]` | 未確認 | なし | なし |
| `CLI: switchport mode access [E1]` | 未確認 | なし | なし |
| `CLI: switchport access vlan 120 [E1]` | 未確認 | なし | なし |
| `CLI: exit [E3]` | 未確認 | なし | なし |
| `interface vlan 120` | 未確認 | なし | なし |
| `ip address 192.0.2.1 255.255.255.0` | 未確認 | なし | なし |
| `no shutdown` | 未確認 | なし | catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269 |
| `exit` | 未確認 | なし | なし |
| `CLI: interface vlan 120 [E3]` | 未確認 | なし | なし |
| `CLI: ip helper-address 198.51.100.50 [E17]` | 未確認 | なし | なし |
| `CLI: exit [E3]` | 未確認 | なし | なし |

## 検索結果

| 順位 | スコア | 文書 | 見出し | 引用ID |
|---:|---:|---|---|---|
| 1 | 7.7290 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure VLANs > How to Configure Normal-Range VLANs > Before you begin | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0075` |
| 2 | 7.0714 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure VLANs > How to Configure Normal-Range VLANs > Before you begin | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0076` |
| 3 | 1.6952 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure VLANs > How to Configure Normal-Range VLANs > Creating or Modifying an Ethernet VLAN | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0074` |
| 4 | 1.0217 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | ACEs and Fragmented and Unfragmented Traffic > ACLs and Switch Stacks > VLAN Map Configuration Guidelines | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-0867` |
| 5 | 0.6493 | [c9300_iosxe26_sys_mgmt_cg](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md) | Disabling MAC Address Learning on VLAN > Before you begin | `c9300_iosxe26_sys_mgmt_cg:section-0069` |
| 6 | 0.5636 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure VLANs > How to Configure Normal-Range VLANs > Deleting a VLAN > Procedure | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0080` |
| 7 | 0.5333 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Configuration Examples for IEEE 802.1x Port-Based Authentication > Example: Configuring VLAN Groups | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1390` |
| 8 | 1.0000 | [c9300_iosxe26_sys_mgmt_cg](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md) | Monitoring and Troubleshooting the Feature > Procedure > Step 2 show archive > Step 5 exit | `c9300_iosxe26_sys_mgmt_cg:section-0552` |
| 9 | 0.4167 | [catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52/manual.md) | Creating a Local SPAN Session and Configuring Incoming Traffic | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52:section-0293` |
| 10 | 0.3786 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | Managing the MAC Address Table > Configuring MAC Threshold Notification Traps > Disabling MAC Address Learning on VLAN > Before you begin | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0095` |
| 11 | 0.3333 | [catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7/manual.md) | Example: Single-VLAN Unicast Mode Bonjour > Figure 16: Single VLAN Unicast Mode Bonjour Network > Verifying Multiple VLAN Unicast Mode Bonjour > Device# show mdns-sd service-policy name LOCAL-AREA-POLICY | `catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7:section-0238` |
| 12 | 0.5000 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | Configuration Replace and Configuration Rollback > How to Use Configuration Replace and Configuration Rollback > Monitoring and Troubleshooting the Feature > Step 5 exit | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0583` |
| 13 | 0.2727 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Information About IEEE 802.1x Port-Based Authentication > Overview of IEEE 802.1x Port-Based Authentication > 802.1x Multiple Authentication Mode | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1293` |
| 14 | 0.2637 | [catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562/manual.md) | How to Configure IGMP Snooping > Enabling IGMP Immediate Leave > Procedure | `catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562:section-0185` |
| 15 | 0.2581 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Configuring 802.1x User Distribution | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1363` |
| 16 | 0.2500 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Information About IEEE 802.1x Port-Based Authentication > Overview of IEEE 802.1x Port-Based Authentication > 802.1x User Distribution Configuration Guidelines | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1316` |
| 17 | 0.2500 | [catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7/manual.md) | Example: Single-VLAN Unicast Mode Bonjour > Figure 16: Single VLAN Unicast Mode Bonjour Network > Device# show mdns-sd summary vlan 10 > Device# show mdns-sd service-policy name LOCAL-AREA-POLICY | `catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7:section-0230` |
| 18 | 0.2500 | [catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea/manual.md) | Configure Cisco TrustSec credentials > Procedure > Step 1 enable > Step 8 exit | `catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0052` |
| 19 | 0.2417 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure Extended-Range VLANs > Creating an Extended-Range VLAN | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0083` |
| 20 | 0.2411 | [catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7/manual.md) | Configuring mDNS Location-Filter | `catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7:section-0180` |
| 21 | 18.4382 | [catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc/manual.md) | How to Configure VLANs > How to Configure Normal-Range VLANs > Assigning Static-Access Ports to a VLAN | `catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0081` |
| 22 | 1.1250 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configure the VTEPs | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-1188` |
| 23 | 0.8333 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Feature History for CoA Support > Information About Interface Templates > Verifying an Interface Template > Verifying all Builtin Templates | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1576` |
| 24 | 0.6729 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | How to Configure MACsec Encryption > Configuring MKA and MACsec > Configuring Switch-to-Host MACsec Encryption | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-0552` |
| 25 | 0.5930 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Example 3: Configuring Optimized Layer 2 Overlay Multicast for IPv4 with Underlay Multicast Replication | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0512` |
| 26 | 0.5426 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Configuration Examples for Port Security | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1736` |
| 27 | 0.5357 | [catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52/manual.md) | How to Configure Interface Templates > Configuring Dynamic Binding of Interface Templates > Before you begin > Figure 1: Configure ISE to Assign Interface Template | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52:section-0064` |
| 28 | 0.5333 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Feature History for CoA Support > Information About Interface Templates > Verifying an Interface Template > Verifying all Builtin Templates | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1578` |
| 29 | 0.5000 | [catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52/manual.md) | How to Configure Interface Templates > Verifying Interface Templates > Example: Verifying all Builtin Templates | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52:section-0070` |
| 30 | 0.5000 | [c9300_iosxe26_int_hw_cg](../products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/manual.md) | Configuring Interface Characteristics > Configuring and Using Interface Range Macros > Configuring a Breakout Interface > Table 4: C9300-NM-2Q Module with two 4x10G Breakout Cables | `c9300_iosxe26_int_hw_cg:section-0059` |
| 31 | 4.1216 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 Routing Proxy on an Interface > Before you begin | `c9300_iosxe26_ip_addr_svc_cg:section-0510` |
| 32 | 0.3879 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for EVPN VXLAN Layer 3 Overlay Network | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0157` |
| 33 | 0.3762 | [catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80/manual.md) | Configuration Examples for EtherChannels > Example: Configuring Layer 2 EtherChannels | `catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80:section-0267` |
| 34 | 0.3699 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuring the Access-Facing Interface in the VLAN on a VTEP > Procedure | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0057` |
| 35 | 0.3667 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Feature History for CoA Support > How to Configure Autoconf > Applying a Built-in Template to an End Device > Verifying Global Configuration after Applying Autoconf | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1606` |
| 36 | 2.2200 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | IPv6 Neighbor Discovery Proxy > How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 Routing Proxy on an Interface > Before you begin | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0517` |
| 37 | 0.3429 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for Private VLANs in a BGP EVPN VXLAN Fabric | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-1048` |
| 38 | 0.3333 | [catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52/manual.md) | SPAN Configuration Guidelines | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52:section-0281` |
| 39 | 1.1148 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | How to Perform Device Setup Configuration > Configuring DHCP Autoconfiguration (Only Configuration File) > Manually Assigning IP Information to Multiple SVIs | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0160` |
| 40 | 2.0000 | [c9300_iosxe26_sys_mgmt_cg](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md) | Configuring DHCP Autoconfiguration (Only Configuration File) | `c9300_iosxe26_sys_mgmt_cg:section-0132` |
| 41 | 4.5000 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | Example: Configuring a Device to Download Configurations from a DHCP Server | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0207` |
| 42 | 1.0000 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | How to Perform Device Setup Configuration > Configuring DHCP Autoconfiguration (Only Configuration File) | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0157` |
| 43 | 4.0895 | [c9300_iosxe26_sys_mgmt_cg](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md) | Manually Assigning IP Information to Multiple SVIs | `c9300_iosxe26_sys_mgmt_cg:section-0134` |
| 44 | 2.7429 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | How to Perform Device Setup Configuration > Configuring DHCP Autoconfiguration (Only Configuration File) > Manually Assigning IP Information to Multiple SVIs | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0162` |
| 45 | 2.5000 | [c9300_iosxe26_sys_mgmt_cg](../products/catalyst9300/packages/c9300_iosxe26_sys_mgmt_cg/manual.md) | Example: Configuring a Device to Download Configurations from a DHCP Server | `c9300_iosxe26_sys_mgmt_cg:section-0180` |
| 46 | 1.0000 | [catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52/manual.md) | Configuring Network Detection and Response | `catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52:section-0583` |
| 47 | 0.6060 | [catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9/manual.md) | Information About VPLS, VPLS BGP-Based Autodiscovery and Flow-Aware Transport > Configuring Layer 2 PE Device Interfaces to CE Devices > Configuring Layer 2 VLAN Instances on a PE Device > Procedure | `catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9:section-0691` |
| 48 | 0.5333 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | How to Perform Device Setup Configuration > Configuring DHCP Autoconfiguration (Only Configuration File) | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0158` |
| 49 | 0.5000 | [catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9/manual.md) | How to configure Seamless MPLs > Configuring Seamless MPLS on the PE Router | `catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9:section-0814` |
| 50 | 0.5000 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Configuring Network Address Translation | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0304` |
| 51 | 0.4429 | [catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9/manual.md) | Information About VPLS, VPLS BGP-Based Autodiscovery and Flow-Aware Transport > Configuring VPLS BGP-based Autodiscovery > Configuring VPLS BGP based Autodiscovery Flow-Aware Transport using Template (in Protocol-CLI Mode) | `catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9:section-0714` |
| 52 | 0.4202 | [catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9/manual.md) | Information About VPLS, VPLS BGP-Based Autodiscovery and Flow-Aware Transport > Configuring VPLS BGP-based Autodiscovery > Configuring VPLS BGP based Autodiscovery in Protocol-CLI mode | `catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9:section-0712` |
| 53 | 0.3333 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Information About DHCP > Enabling DHCP Server Port-Based Address Allocation > Procedure > What to do next | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0102` |
| 54 | 0.3333 | [catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9/manual.md) | How to configure Seamless MPLs > Configuring Seamless MPLS on the PE Router | `catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9:section-0813` |
| 55 | 0.3333 | [catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80/manual.md) | How to Configure Layer 2 Protocol Tunneling for EtherChannels > Configuring the Customer Device > Before you begin | `catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80:section-0448` |
| 56 | 0.3333 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuring EVPN VXLAN Layer 3 Overlay Network | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0138` |
| 57 | 0.3226 | [catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/manual.md) | How to Perform Device Setup Configuration > Configuring DHCP Autoconfiguration (Only Configuration File) > Manually Assigning IP Information to Multiple SVIs | `catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f:section-0159` |
| 58 | 0.3015 | [catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea/manual.md) | How to Configure SGT Mapping > Configuring a Device SGT Manually > Configuring VLAN-to-SGT Mapping | `catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea:section-0269` |
| 59 | 6.5600 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Information About DHCP > How to Configure DHCP > Specifying the Packet Forwarding Address | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0082` |
| 60 | 3.6559 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Information About DHCP > Configuring DHCP Snooping on Private VLANs | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0126` |
| 61 | 3.5444 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure DHCP > Specifying the Packet Forwarding Address | `c9300_iosxe26_ip_addr_svc_cg:section-0087` |
| 62 | 3.0000 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | Configuration Examples for TCP MSS Adjustment > Example: Configuring the TCP MSS Adjustment | `c9300_iosxe26_ip_addr_svc_cg:section-0481` |
| 63 | 2.5568 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | Configuring DHCP Snooping on Private VLANs | `c9300_iosxe26_ip_addr_svc_cg:section-0131` |
| 64 | 1.5455 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 DAD Proxy on an Interface > Before you begin | `c9300_iosxe26_ip_addr_svc_cg:section-0515` |
| 65 | 1.5000 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Configuring TCP MSS Adjustment > Configuration Examples for TCP MSS Adjustment > Example: Configuring the TCP MSS Adjustment | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0488` |
| 66 | 1.2389 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | IPv6 Neighbor Discovery Proxy > How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 DAD Proxy on an Interface > Before you begin | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0522` |
| 67 | 0.8890 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 Routing Proxy in VLAN Configuration Mode > Before you begin | `c9300_iosxe26_ip_addr_svc_cg:section-0508` |
| 68 | 0.7778 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | IPv6 Neighbor Discovery Proxy > How to Configure IPv6 Neighbor Discovery Proxy > Configuring IPv6 Routing Proxy in VLAN Configuration Mode > Before you begin | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0515` |
| 69 | 0.7647 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure DHCP > Specifying the Packet Forwarding Address > DHCPv6 Address Assignment Configuration Guidelines | `c9300_iosxe26_ip_addr_svc_cg:section-0090` |
| 70 | 0.7500 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure IPv6 Client Address Learning > Configuring Stateless Auto Address Configuration With DHCP | `c9300_iosxe26_ip_addr_svc_cg:section-0048` |
| 71 | 0.7500 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Configuring WCCP > Configuring WCCP | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0402` |
| 72 | 0.7213 | [catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b/manual.md) | Configuring IP Unicast Routing > Configuring Broadcast Packet Handling > Enabling Directed Broadcast-to-Physical Broadcast Translation > Forwarding UDP Broadcast Packets and Protocols | `catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b:section-0172` |
| 73 | 0.5881 | [catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/manual.md) | Information About DHCP > How to Configure DHCP > DHCPv6 Address Assignment Configuration Guidelines | `catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d:section-0085` |
| 74 | 0.5000 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure IPv6 Client Address Learning > Configuring IPv6 ND Suppress Policy > Configuring Stateless Auto Address Configuration Without DHCP | `c9300_iosxe26_ip_addr_svc_cg:section-0047` |
| 75 | 0.5000 | [c9300_iosxe26_ip_addr_svc_cg](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md) | How to Configure Hot Standby Router Protocol > Default HSRP Configuration > Enabling and Verifying an HSRP Group for IPv6 Operation | `c9300_iosxe26_ip_addr_svc_cg:section-0247` |
