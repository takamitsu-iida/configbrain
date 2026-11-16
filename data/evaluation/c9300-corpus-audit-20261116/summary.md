# Local PDF / OKF corpus audit

Local heuristic audit, not approval of semantic correctness or completeness. Text normalization ignores spacing, case and punctuation; CLI checks also compare punctuation but PDF columns and wrapping can cause false positives. Page windows are inferred from unique text anchors and overlap at boundaries. Repeated lines and standalone page numbers in the outer 6% PDF margins are excluded from text coverage and recorded. Images, diagrams, OCR and the meaning of tables require visual review. No API calls, repairs, package writes or index updates are performed.

- Run status: completed
- Documents: 18 / 18
- PDF pages (including duplicates): 6502
- Page statuses: {"checked": 664, "review_required": 4768, "unverifiable": 1070}

| Document | Status | Pages | Unverifiable | Page findings | Report |
| --- | --- | ---: | ---: | ---: | --- |
| c9300_iosxe26_int_hw_cg | review_required | 198 | 14 | 487 | [JSON](documents/c9300_iosxe26_int_hw_cg.json) |
| c9300_iosxe26_ip_addr_svc_cg | review_required | 250 | 34 | 640 | [JSON](documents/c9300_iosxe26_ip_addr_svc_cg.json) |
| c9300_iosxe26_sys_mgmt_cg | review_required | 396 | 51 | 904 | [JSON](documents/c9300_iosxe26_sys_mgmt_cg.json) |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | review_required | 1298 | 430 | 1773 | [JSON](documents/catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321.json) |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | review_required | 110 | 21 | 204 | [JSON](documents/catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7.json) |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | review_required | 136 | 13 | 318 | [JSON](documents/catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea.json) |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | review_required | 198 | 15 | 488 | [JSON](documents/catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f.json) |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | review_required | 250 | 29 | 635 | [JSON](documents/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d.json) |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | review_required | 446 | 47 | 1109 | [JSON](documents/catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562.json) |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | review_required | 298 | 21 | 758 | [JSON](documents/catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80.json) |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | review_required | 406 | 88 | 881 | [JSON](documents/catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9.json) |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | review_required | 292 | 29 | 687 | [JSON](documents/catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52.json) |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | review_required | 148 | 28 | 327 | [JSON](documents/catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a.json) |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | review_required | 470 | 67 | 1173 | [JSON](documents/catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b.json) |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | review_required | 964 | 96 | 2619 | [JSON](documents/catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2.json) |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | review_required | 122 | 16 | 275 | [JSON](documents/catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2.json) |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | review_required | 394 | 52 | 833 | [JSON](documents/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f.json) |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | review_required | 126 | 19 | 311 | [JSON](documents/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc.json) |

## Finding counts

- extracted_structure: 1955
- extracted_to_package: 431
- package_structure: 1744
- pdf_margin_coordinates_unavailable: 2
- pdf_text_fragments_not_found: 9413
- possible_missing_constraint_or_alert: 347
- possible_missing_or_changed_prompt_command: 766
- possible_transformation_text_change: 2834
- visual_review_required: 1060

## Review queue (up to five priority pages per document)

All pages and all findings remain in each document JSON; this queue is only a shortlist.

| Document | PDF page | Status | OKF lines (inferred context) | High priority |
| --- | ---: | --- | --- | ---: |
| c9300_iosxe26_int_hw_cg | 148 | review_required | [5673, 5723] | 8 |
| c9300_iosxe26_int_hw_cg | 47 | review_required | [1662, 1674] | 6 |
| c9300_iosxe26_int_hw_cg | 38 | review_required | [1240, 1351] | 5 |
| c9300_iosxe26_int_hw_cg | 98 | review_required | [3748, 3829] | 4 |
| c9300_iosxe26_int_hw_cg | 189 | review_required | [7314, 7379] | 4 |
| c9300_iosxe26_ip_addr_svc_cg | 54 | review_required | [1593, 1595] | 7 |
| c9300_iosxe26_ip_addr_svc_cg | 40 | review_required | [1142, 1178] | 6 |
| c9300_iosxe26_ip_addr_svc_cg | 79 | review_required | [2587, 2598] | 6 |
| c9300_iosxe26_ip_addr_svc_cg | 164 | review_required | [5755, 6009] | 5 |
| c9300_iosxe26_ip_addr_svc_cg | 101 | review_required | [3374, 3457] | 5 |
| c9300_iosxe26_sys_mgmt_cg | 249 | review_required | [9785, 9846] | 8 |
| c9300_iosxe26_sys_mgmt_cg | 377 | review_required | [15253, 15311] | 7 |
| c9300_iosxe26_sys_mgmt_cg | 179 | review_required | [6965, 7015] | 6 |
| c9300_iosxe26_sys_mgmt_cg | 34 | review_required | [931, 1007] | 6 |
| c9300_iosxe26_sys_mgmt_cg | 38 | review_required | [1140, 1241] | 5 |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 708 | review_required | [37910, 37932] | 6 |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 41 | review_required | [1138, 1181] | 5 |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 42 | review_required | [1169, 1211] | 5 |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 110 | review_required | [4671, 4789] | 5 |
| catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321 | 676 | review_required | [35921, 35974] | 5 |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 51 | review_required | [1671, 1723] | 5 |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 100 | review_required | [3557, 3639] | 4 |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 50 | review_required | [1652, 1680] | 4 |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 54 | review_required | [1817, 1869] | 3 |
| catalyst9300-26-x-b-26x-bonjour-9300-cg-ee5da72ad5b7 | 56 | review_required | [1897, 1983] | 3 |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 57 | review_required | [1977, 2027] | 4 |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 42 | review_required | [1344, 1427] | 4 |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 43 | review_required | [1409, 1487] | 4 |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 82 | review_required | [2916, 2972] | 3 |
| catalyst9300-26-x-b-26x-cts-9300-cg-5f2b2b664dea | 40 | review_required | [1243, 1308] | 3 |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 47 | review_required | [1524, 1536] | 6 |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 173 | review_required | [6430, 6487] | 5 |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 104 | review_required | [3939, 3959] | 5 |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 105 | review_required | [3950, 4000] | 5 |
| catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f | 172 | review_required | [6390, 6438] | 5 |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 79 | review_required | [2419, 2429] | 7 |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 54 | review_required | [1484, 1488] | 7 |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 162 | review_required | [5227, 5248] | 6 |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 170 | review_required | [5491, 5523] | 6 |
| catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d | 99 | review_required | [3022, 3088] | 5 |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 234 | review_required | [7760, 7775] | 7 |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 96 | review_required | [2848, 2896] | 6 |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 191 | review_required | [6316, 6359] | 6 |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 203 | review_required | [6784, 6816] | 6 |
| catalyst9300-26-x-b-26x-ip-mcast-rtng-9300-cg-b076db950562 | 341 | review_required | [11837, 11906] | 5 |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 34 | review_required | [837, 879] | 6 |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 122 | review_required | [3638, 3678] | 6 |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 152 | review_required | [4690, 4748] | 5 |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 30 | review_required | [623, 685] | 5 |
| catalyst9300-26-x-b-26x-lyr2-9300-cg-db110697da80 | 32 | review_required | [738, 789] | 5 |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 399 | review_required | [14661, 14749] | 7 |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 375 | review_required | [13526, 13576] | 6 |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 242 | review_required | [8719, 8738] | 6 |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 261 | review_required | [9299, 9425] | 6 |
| catalyst9300-26-x-b-26x-mpls-9300-cg-b8c1ee43d3e9 | 267 | review_required | [9535, 9582] | 5 |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 32 | review_required | [1119, 1191] | 6 |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 94 | review_required | [3690, 3766] | 5 |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 95 | review_required | [3754, 3801] | 5 |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 114 | review_required | [4388, 4455] | 5 |
| catalyst9300-26-x-b-26x-nmgmt-9300-cg-3e060c1d1d52 | 204 | review_required | [8119, 8181] | 5 |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 108 | review_required | [4371, 4425] | 6 |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 94 | review_required | [3736, 3790] | 5 |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 98 | review_required | [3912, 3971] | 5 |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 101 | review_required | [4045, 4173] | 5 |
| catalyst9300-26-x-b-26x-qos-9300-cg-4e21dc6d1e2a | 85 | review_required | [3345, 3403] | 5 |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 201 | review_required | [7398, 7406] | 8 |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 141 | review_required | [5154, 5239] | 8 |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 384 | review_required | [14263, 14268] | 7 |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 255 | review_required | [9246, 9265] | 7 |
| catalyst9300-26-x-b-26x-rtng-9300-cg-e1501a58353b | 318 | review_required | [11775, 11857] | 7 |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 104 | review_required | [3449, 3514] | 10 |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 469 | review_required | [18793, 18817] | 7 |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 106 | review_required | [3562, 3625] | 6 |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 533 | review_required | [21344, 21394] | 6 |
| catalyst9300-26-x-b-26x-sec-9300-cg-8e2ad3aabae2 | 568 | review_required | [22666, 22728] | 6 |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 84 | review_required | [2539, 2557] | 7 |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 25 | review_required | [677, 727] | 6 |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 94 | review_required | [3000, 3040] | 6 |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 101 | review_required | [3190, 3444] | 4 |
| catalyst9300-26-x-b-26x-stck-mgr-ha-9300-cg-b916a4af4da2 | 51 | review_required | [1535, 1607] | 3 |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 375 | review_required | [14866, 14922] | 7 |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 34 | review_required | [886, 936] | 6 |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 174 | review_required | [6839, 6870] | 5 |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 31 | review_required | [792, 839] | 5 |
| catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f | 123 | review_required | [4493, 4544] | 5 |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 103 | review_required | [3413, 3565] | 6 |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 98 | review_required | [3298, 3366] | 5 |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 99 | review_required | [3364, 3527] | 5 |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 47 | review_required | [1510, 1557] | 4 |
| catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc | 56 | review_required | [1806, 1845] | 4 |

## Duplicate source URLs

- https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg.pdf: c9300_iosxe26_int_hw_cg, catalyst9300-26-x-b-26x-int-and-hw-9300-cg-2a392382640f
- https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/ip/b_26x_ip_9300_cg.pdf: c9300_iosxe26_ip_addr_svc_cg, catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d
- https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sys_mgmt/b_26x_sys_mgmt_9300_cg.pdf: c9300_iosxe26_sys_mgmt_cg, catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f
