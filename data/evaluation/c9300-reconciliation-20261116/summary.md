# コマンド例・表の列不整合の全件照合

All selected audit candidates are reconciled locally, without API calls or repairs. Verdicts concern the recorded example text or Markdown serialization, not semantic correctness. A short table row can be valid Markdown with implicit empty cells; column-count variation alone does not prove content loss. PDF command continuations are inferred from coordinates. Heading/page IDs and the audit's inferred ranges bound searches; uncertain correspondence remains unresolved. Finding matching text elsewhere in the manual does not dismiss a candidate. PDF text coordinates are not a substitute for visual review of ambiguous cells.

- 対象文書: 18
- コマンド候補: 766
- 表候補: 110
- 判定: `{"confirmed_text_difference": 38, "audit_false_positive": 177, "unresolved": 418, "confirmed_formatting_difference": 133, "confirmed_column_count_variation": 110}`
- 表とPDF本文の対応: `{"cell_text_matched": 97, "unresolved": 13}`
- 表の行分類（重複あり）: `{"short_row": 218, "embedded_code_fence": 215, "empty_stub": 33, "extra_cells": 12}`

[全候補の一覧CSV](review_queue.csv)には誤検出と保留を含む全件を保存しています。

## 判定の意味

- `audit_false_positive`: 対応範囲に例文を確認。説明列の混入等による検出候補。
- `confirmed_formatting_difference`: コマンドが複数セル・行へ分断、または例のプロンプトを喪失。
- `confirmed_text_difference`: PDFのコマンド例を対応セクション内に確認できない。
- `confirmed_column_count_variation`: 表の列数の揺れを確認。内容欠落の確定ではない。
- `unresolved`: 対応範囲・出現箇所・折り返し等が曖昧で判断保留。

短い行はMarkdownの空セル補完により表示できる場合があります。
コードフェンス混入・余剰セルと、空セル省略を区別して詳細JSONへ記録しています。

## 文書別結果

| 文書 | コマンド | 表 | 詳細 |
| --- | ---: | ---: | --- |
| c9300_iosxe26_int_hw_cg | 20 | 0 | [JSON](documents/c9300_iosxe26_int_hw_cg.json) |
| c9300_iosxe26_ip_addr_svc_cg | 46 | 7 | [JSON](documents/c9300_iosxe26_ip_addr_svc_cg.json) |
| c9300_iosxe26_sys_mgmt_cg | 65 | 0 | [JSON](documents/c9300_iosxe26_sys_mgmt_cg.json) |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 67 | 13 | [JSON](documents/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321.json) |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 4 | 0 | [JSON](documents/catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7.json) |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 9 | 1 | [JSON](documents/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea.json) |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 21 | 0 | [JSON](documents/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f.json) |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 50 | 10 | [JSON](documents/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d.json) |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 53 | 4 | [JSON](documents/catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562.json) |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 34 | 9 | [JSON](documents/catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80.json) |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 63 | 9 | [JSON](documents/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9.json) |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 22 | 10 | [JSON](documents/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52.json) |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 24 | 1 | [JSON](documents/catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a.json) |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 93 | 24 | [JSON](documents/catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b.json) |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 119 | 12 | [JSON](documents/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2.json) |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 14 | 0 | [JSON](documents/catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2.json) |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 47 | 8 | [JSON](documents/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f.json) |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 15 | 2 | [JSON](documents/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc.json) |

## コマンド例の本文相違

セル分断・誤検出・判断保留の全件詳細はCSVと文書別JSONを参照してください。

### c9300_iosxe26_int_hw_cg / PDF 38ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `copy running-config startup-config`
- OKF行: [1238, 1267]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_int_hw_cg / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `copy running-config startup-config`
- OKF行: [1646, 1665]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_int_hw_cg / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip address 192.20.135.21 255.255.255.0`
- OKF行: [1646, 1665]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_int_hw_cg / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no shutdown`
- OKF行: [1646, 1665]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_int_hw_cg / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no switchport`
- OKF行: [1646, 1665]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 40ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip address 192.168.20.1 255.255.255.0`
- OKF行: [1127, 1164]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 40ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 dhcp relay destination 2001:DB8:0:20::2`
- OKF行: [1127, 1164]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 40ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 nd prefix 2001:db8::/64 no-advertise`
- OKF行: [1127, 1164]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 61ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip dhcp snooping binding interface gigabitethernet 1/1/0 expiry`
- OKF行: [1846, 1924]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `interface GigabitEthernet`
- OKF行: [2567, 2588]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 nd prefix 2001:DB8::1/64 1111 222`
- OKF行: [2567, 2588]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 nd ra lifetime 9000`
- OKF行: [2567, 2588]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no switchport`
- OKF行: [2567, 2588]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 162ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `configure terminal`
- OKF行: [5751, 5872]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 162ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip nat pool net-208 172.16.233.208 172.16.233.223`
- OKF行: [5751, 5872]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 170ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `interface ethernet 0`
- OKF行: [6013, 6055]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_ip_addr_svc_cg / PDF 203ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip pim dense-mode`
- OKF行: [7351, 7352]
- 原因段階の候補: `extracted_to_package`

### c9300_iosxe26_ip_addr_svc_cg / PDF 219ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `object 1`
- OKF行: [7931, 7968]
- 原因段階の候補: `extracted_to_package`

### c9300_iosxe26_sys_mgmt_cg / PDF 176ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `configure terminal`
- OKF行: [6875, 6914]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_sys_mgmt_cg / PDF 176ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `line console`
- OKF行: [6875, 6914]
- 原因段階の候補: `pdf_to_extracted`

### c9300_iosxe26_sys_mgmt_cg / PDF 292ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `reload fast b`
- OKF行: [11412, 11549]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 / PDF 42ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `vlan configuration 11`
- OKF行: [1192, 1210]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f / PDF 38ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `copy running-config startup-config`
- OKF行: [1154, 1170]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `copy running-config startup-config`
- OKF行: [1508, 1527]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip address 192.20.135.21 255.255.255.0`
- OKF行: [1508, 1527]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no shutdown`
- OKF行: [1508, 1527]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f / PDF 47ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no switchport`
- OKF行: [1508, 1527]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d / PDF 40ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 dhcp relay destination 2001:DB8:0:20::2`
- OKF行: [1100, 1137]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `interface GigabitEthernet`
- OKF行: [2372, 2420]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 nd prefix 2001:DB8::1/64 1111 222`
- OKF行: [2372, 2420]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ipv6 nd ra lifetime 9000`
- OKF行: [2372, 2420]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d / PDF 79ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `no switchport`
- OKF行: [2372, 2420]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 / PDF 234ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `ip msdp sa-limit 192.168.10.1 100`
- OKF行: [7591, 7772]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 / PDF 234ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `show ip msdp count`
- OKF行: [7591, 7772]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 / PDF 234ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `show ip msdp summary`
- OKF行: [7591, 7772]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 / PDF 30ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `spanning-tree mode pvst`
- OKF行: [625, 692]
- 原因段階の候補: `extracted_to_package`

### catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 / PDF 36ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `spanning-tree vlan 20 priority 8192`
- OKF行: [915, 926]
- 原因段階の候補: `pdf_to_extracted`

### catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 / PDF 717ページ

- 判定: `confirmed_text_difference` / `command_example_absent_in_corresponding_section`
- PDF: `show ip admission status custom-pages`
- OKF行: [27984, 28442]
- 原因段階の候補: `pdf_to_extracted`

