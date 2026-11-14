# Cisco RAG生成レポート

## 質問

Catalyst 9300のVlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送してください。

## 生成コンフィグ

```text
interface Vlan100
 ip helper-address 192.0.2.50
```

## 説明

要件は「Vlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送する」ことです。

根拠では、DHCPリレーの実装方法としてSVI（Vlanインターフェイス）上で `ip helper-address` を設定する例が複数示されています。

- 同一テナントVRF内でのDHCPリレー例では、Vlanインターフェイスに対して以下のように設定しています。
  - `interface Vlan101`
  - ` ip dhcp relay source-interface Loopback101`
  - ` ip address 10.1.101.1 255.255.255.0`
  - ` ip helper-address 192.168.20.20`
  （引用ID: catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0373）

- DHCPサーバーがデフォルトVRF、クライアントがテナントVRFにいる例では、Vlanインターフェイスに対して以下のように設定しています。
  - `interface Vlan101`
  - ` vrf forwarding green`
  - ` ip address 10.1.101.1 255.255.255.0`
  - ` ip helper-address global 192.168.20.20`
  （引用ID: catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0370）

これらの根拠から、Catalyst 9300 / IOS XE 26.x において、Vlanインターフェイス上の `ip helper-address` がDHCP要求の転送先（DHCPサーバー）を指定するために使用されることが確認できます。

要件ではVRF構成や`global`/`vrf`付きのヘルパー指定についての情報が与えられていないため、根拠にある構文のうち、最も基本的な `ip helper-address <DHCPサーバーIP>` の形式のみを使用し、Vlan100のSVI上に `ip helper-address 192.0.2.50` を設定する構成としました。

VRFやループバックインターフェイス、`ip dhcp relay information option` などの追加設定は、要件にも根拠にもVlan100との関係が明示されていないため、推測を避けて設定していません。

## 注意事項

- Vlan100のVRF名やIPアドレス、既存のDHCPリレー/ヘルパー設定有無などの情報が提供されていないため、最小限の `ip helper-address 192.0.2.50` のみを設定しています。
- DHCPサーバー192.0.2.50がどのVRFに存在するか不明なため、根拠にある `ip helper-address global` や `ip helper-address vrf <name>` の構文は使用していません。
- DHCPリレーのソースインターフェイス（例: Loopbackインターフェイス）に関する要件や根拠がVlan100に対して示されていないため、`ip dhcp relay source-interface` は設定していません。
- 全体のDHCPリレー機能 (`ip dhcp-relay source-interface` や `ip dhcp relay information option` など) の有効化状況は不明であり、既存設定との整合性は別途確認が必要です。

## 参照根拠

- [`catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0373`](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md)
- [`catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0370`](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md)
- [`c9300_iosxe26_int_hw_cg:section-0017`](../products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/manual.md)

## 根拠外の推測

- 知識利用ポリシー: `disallow`
- 推測コマンド数: 0

## 検索結果

| 順位 | スコア | 文書 | 見出し | 引用ID |
|---:|---:|---|---|---|
| 1 | 1.3921 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server and DHCP Client are in the Same Tenant VRF > DHCP Configuration Snippet | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0373` |
| 2 | 1.0441 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server is in the Layer 3 Default VRF and the DHCP Client is in the Tenant VRF > VTEP 1 | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0370` |
| 3 | 1.0000 | [c9300_iosxe26_int_hw_cg](../products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/manual.md) | Configuring Interface Characteristics > Information About Interface Characteristics > Switch Ports > Switch Virtual Interfaces | `c9300_iosxe26_int_hw_cg:section-0017` |
| 4 | 1.0000 | [catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2](../products/catalyst9300/packages/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2/manual.md) | Monitoring IPv4 ACLs > Table 22: Commands for Displaying Access Lists and Access Groups | `catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2:section-0912` |
| 5 | 0.5595 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server is in the Layer 3 Default VRF and the DHCP Client is in the Tenant VRF > DHCP Configuration Snippet | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0368` |
| 6 | 0.5429 | [catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321](../products/catalyst9300/packages/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Example: DHCP Client and DHCP Server are in Different Tenant VRFs > DHCP Configuration Snippet | `catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0377` |
