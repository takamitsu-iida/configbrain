# Cisco RAG生成レポート

## 質問

GigabitEthernet1/0/20をアクセスポートにしてVLAN 120へ所属させてください。

## 生成コンフィグ

```text
interface GigabitEthernet1/0/20
 switchport access vlan 120
 switchport mode access
```

## 説明

根拠のコンフィグ例では、アクセスポートの設定として以下の構文が確認できます。
- `interface GigabitEthernet1/0/11` などのインターフェース指定
- `switchport access vlan <VLAN番号>` でアクセスポートの所属VLANを指定
- `switchport mode access` でポートをアクセスポートモードに設定

これらを要件に合わせてGigabitEthernet1/0/20およびVLAN 120に置き換えた設定のみを生成しています。

## 注意事項

- VLAN 120自体の作成（例: `vlan 120` やSVI設定など）に関する構文は根拠内に存在しないため、VLAN 120が既にスイッチ上で作成済みであることを前提としています。VLAN作成コマンドは根拠がないため生成していません。
- コンフィグモードへの遷移（`configure terminal`）や設定保存コマンドについて、要件および根拠に明示がないため生成していません。

## 参照根拠

- [`catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-1188`](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md)
- [`catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0512`](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md)

## 根拠外の推測

- 知識利用ポリシー: `disallow`
- 推測コマンド数: 0

## 検索結果

| 順位 | スコア | 文書 | 見出し | 引用ID |
|---:|---:|---|---|---|
| 1 | 2.0390 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configure the VTEPs | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-1188` |
| 2 | 1.0000 | [c9300_iosxe26_int_hw_cg](../products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/manual.md) | Configuring Interface Characteristics > Configuring and Using Interface Range Macros > Configuring a Breakout Interface > Table 4: C9300-NM-2Q Module with two 4x10G Breakout Cables | `c9300_iosxe26_int_hw_cg:section-0059` |
| 3 | 0.5806 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Example 3: Configuring Optimized Layer 2 Overlay Multicast for IPv4 with Underlay Multicast Replication | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0512` |
| 4 | 0.5385 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Feature History for CoA Support > Information About Interface Templates > Verifying an Interface Template > Verifying all Builtin Templates | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-1576` |
| 5 | 0.5000 | [catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a/manual.md) | How to Configure Class, Policy, and Maps > Configuring Class-Based Packet Marking > Attaching a Traffic Policy to an Interface > Before you begin | `catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a:section-0195` |
| 6 | 0.3333 | [c9300_iosxe26_int_hw_cg](../products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/manual.md) | Configuring Interface Characteristics > Configuring and Using Interface Range Macros > C9300-NM-2C and C9300-NM-4C Network Modules > Table 8: C9300-NM-4C Module with two 4x25G Breakout Cables | `c9300_iosxe26_int_hw_cg:section-0064` |
