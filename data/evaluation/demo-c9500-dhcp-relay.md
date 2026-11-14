# Cisco RAG生成レポート

## 質問

Catalyst 9500のVlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送してください。

## 生成コンフィグ

```text
interface Vlan100
 ip helper-address 192.0.2.50
```

## 説明

要件は「Vlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送する」ことなので、SVIであるVlanインターフェースにDHCPリレー（ip helper-address）を設定します。

根拠では、DHCPリレーの具体例としてVlanインターフェース上にip helper-addressを設定する方法が示されています。
- Vlan上に直接ヘルパーアドレスを設定する例:
  - interface Vlan101 / Vlan102 / Vlan201 / Vlan202 に対して
    - ip helper-address 192.168.20.20 または ip helper-address global 192.168.20.20 が設定されていることから、Vlanインターフェースにip helper-addressを設定することで、そのVlanで受信したDHCPブロードキャストを指定DHCPサーバーへ転送できることが分かります。[catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0416][catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0413][catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0411]

今回の要件ではVRF名やLoopbackインターフェース、DHCP snoopingなどの追加条件は指定されていないため、根拠にある構文のうち、要件達成に必須であり、かつ構文が明示されている「interface Vlan100」および「ip helper-address <DHCPサーバーIP>」のみを使用しています。

## 注意事項

- Vlan100の存在有無やSVIのIPアドレス、VRF設定（例: vrf forwarding green/red）について根拠がなく、要件にも指定がないため設定していません。必要な場合は別途定義してください。
- DHCPリレーの詳細オプション（ip dhcp relay information option vpn や ip dhcp snooping など）については、要件に明示がなく、かつVlan100に対する具体的な構文が根拠に示されていないため、本回答では設定していません。
- ip helper-address global の構文は根拠に存在しますが、globalキーワードの使用要件（デフォルトVRFとテナントVRFの関係など）が今回の要件からは読み取れないため、単純な ip helper-address 192.0.2.50 のみを使用しています。

## 参照根拠

- [`catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0416`](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md)
- [`catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0413`](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md)
- [`catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0411`](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md)

## 根拠外の推測

- 知識利用ポリシー: `disallow`
- 推測コマンド数: 0

## 検索結果

| 順位 | スコア | 文書 | 見出し | 引用ID |
|---:|---:|---|---|---|
| 1 | 1.3333 | [catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server and DHCP Client are in the Same Tenant VRF > DHCP Configuration Snippet | `catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0416` |
| 2 | 1.0000 | [catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server is in the Layer 3 Default VRF and the DHCP Client is in the Tenant VRF > VTEP 1 | `catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0413` |
| 3 | 1.0000 | [catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843/manual.md) | Information About Interface Characteristics > Interface Types > Port-Based VLANs > Switch Virtual Interfaces | `catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843:section-0021` |
| 4 | 1.0000 | [catalyst9500-26-x-b-26x-ip-9500-cg-51c9dc4dd233](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ip-9500-cg-51c9dc4dd233/manual.md) | How to Configure DHCP > Configuring the DHCP Server > Enabling DHCP Server Port-Based Address Allocation | `catalyst9500-26-x-b-26x-ip-9500-cg-51c9dc4dd233:section-0126` |
| 5 | 0.5469 | [catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md) | How to Configure DHCP Relay in a BGP EVPN VXLAN Fabric > Configuring DHCP Relay on a VTEP > Procedure | `catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0398` |
| 6 | 0.5000 | [catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600](../products/catalyst9500/packages/catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600/manual.md) | Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric > Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric > Example: DHCP Server is in the Layer 3 Default VRF and the DHCP Client is in the Tenant VRF > DHCP Configuration Snippet | `catalyst9500-26-x-26x-bgp-evpn-vxlan-9500-cg-3965c8319600:section-0411` |
