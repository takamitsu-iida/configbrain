# Stateful DHCP補正候補とPDF原本の照合

## 結論

**全14手順のコマンド・例・説明、および導入文とパスワード入力条件の保持を確認した。**
欠落した手順や、原本にない設定手順の追加は見つからなかった。
ただし、文字列の完全一致ではない。折り返し・空白・表から手順見出しへの整形に加え、
Step 14のCommand or Action欄の語間は、同じPDFのExampleに合わせている。
原本自体の説明・表記にある不整合と、変換による欠落を区別する必要がある。

これはAIによる原本との局所的な照合であり、人手による確認・機器上の動作検証や、
文書全体の品質承認ではない。補正の状態は`pending_review`のままとし、
本文への適用、パッケージ再作成、索引更新は行っていない。

## 対象と方法

- 原本：[IP Addressing Services Configuration Guide](../../products/catalyst9300/raw/catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d/b_26x_ip_9300_cg.pdf)、
  物理ページ39〜40、印刷ページ21〜22。
- 節：Configuring Stateful DHCP Externally。
- 候補：[stateful-dhcp.md](../../corrections/c9300-stateful-dhcp/stateful-dhcp.md)。
- [補正定義](../../corrections/c9300-stateful-dhcp/corrections.json)が指定する
  元Markdownの951〜988行を対象とする。
- PDFのSHA-256：
  `158e784d02aee08881c68010114559a373b810ec41c6cb6a2c3ba5daef4320f1`。
- 候補のSHA-256：
  `eaca8533620bff3261dc51f8ea0d4a0c99f02da5c357a5a790471b78ab3dffa5`。
- PDFのテキストレイヤーに加え、両ページを2倍で描画した
  [ページ39](evidence/pdf-page-39.png)・[ページ40](evidence/pdf-page-40.png)を直接確認した。
  行の読み順だけでなく、罫線で区切られた手順・コマンド・説明の対応を確認した。
- 原本を画像で確認して読み取ったコマンド・例・説明と、候補から抽出した各項目を
  [comparison.json](comparison.json)に保存した。これらの比較は外部APIを使用していない。

## 手順別の照合

以下の「保持」はコマンド、プロンプトを含む例、Purposeのすべてを対象とする。
コマンド欄と例の値、大文字・小文字、アドレス、プレフィックス長、ハイフンを確認した。

| 手順 | 物理PDFページ | コマンド | 結果・確認事項 |
| --- | --- | --- | --- |
| 1 | 39 | `enable` | 保持。`Device>`と、求められた場合のパスワード入力条件も保持。 |
| 2 | 39 | `configure terminal` | 保持。例のプロンプトは`Device#`。 |
| 3 | 39 | `ipv6 unicast-routing` | 保持。例のプロンプトは`Device(config)#`。 |
| 4 | 39 | `dns-server 2001:100:0:1::1` | 保持。例の次行にあったアドレスを結合し、説明末尾の`clients.`も保持。 |
| 5 | 39 | `domain-name example.com` | 保持。例の次行にあったドメイン名を結合し、説明末尾の`clients.`も保持。 |
| 6 | 39 | `exit` | 保持。元の`Device(config-dhcpv6)#`を変更していない。 |
| 7 | 39 | `interface vlan 1` | 保持。VLAN番号、説明、`Device(config)#`を保持。 |
| 8 | 39 | `description IPv6-DHCP-Stateful` | 保持。例の次行の説明文字列を結合。大文字・小文字も保持。 |
| 9 | 39〜40 | `ipv6 address 2001:DB8:0:20::1/64` | 保持。ページ40冒頭の例をStep 9の続きと確認し、Step 10へ誤帰属させていない。 |
| 10 | 40 | `ip address 192.168.20.1 255.255.255.0` | 保持。Purposeの不整合も原文どおり。下記注意点1を参照。 |
| 11 | 40 | `ipv6 nd prefix 2001:db8::/64 no-advertise` | 保持。小文字`db8`と`no-advertise`、否定を含む説明を保持。 |
| 12 | 40 | `ipv6 nd managed-config-flag` | 保持。例の折り返しを結合し、アドレス設定用という説明を保持。 |
| 13 | 40 | `ipv6 nd other-config-flag` | 保持。例の折り返しを結合し、`non-address configuration`をStep 12と区別して保持。 |
| 14 | 40 | `ipv6 dhcp relay destination 2001:DB8:0:20::2` | 原本の例に合致。Command or Action欄とは語間が異なる。下記注意点2を参照。 |

## 原本の注意点と、意図した差異

1. **Step 10の説明は原本自体がIPv6と記載している。**
   コマンドは`ip address 192.168.20.1 255.255.255.0`だが、Purposeは
   `Enters the IPv6 address for the stateful IPv6 DHCP.`である。
   候補は原本の説明を保持した。PDF抽出ミスとしてIPv4へ書き換えてはいない。
2. **Step 14の原本コマンド欄は`ipv6 dhcp relaydestination`と印刷されている。**
   一方、同じ手順のExampleは`Device(config-if)# ipv6 dhcp relay`に続き、
   次行に`destination 2001:DB8:0:20::2`とある。
   候補はこの例を1行へ結合した表記をコマンド欄にも採用している。
   外部のCLI知識による補完ではないが、コマンド欄の逐字転記ではない。
   この表記上の判断は補正定義の`reason`にも既に記録されている。
3. **モード切替など、原本にない手順は補っていない。**
   Step 3の例は`Device(config)#`、Step 4〜6の例は`Device(config-dhcpv6)#`だが、
   その間のモード切替操作はこの手順表に記載されていない。
   候補でも未追加である。原本との対応を確認したことは、そのまま機器へ投入できる
   一連の設定として正しいことを保証しない。
4. Step 12〜13のPDFテキストでは`ConfiguresIPv6`と連結されるが、
   描画された原本では単語の区切りを確認できるため、候補の`Configures IPv6`を維持した。
   `hosts to uses DHCP`という原文の文法は変更していない。
5. PDFの表・太字・折り返しをMarkdownの手順見出し・インラインコード・
   フェンス付きの例へ変更している。PDFのページヘッダー・フッター・印刷ページ番号は
   手順本文に含めない。既存の外側の見出しとアンカーは維持した。

## 範囲と完全性

- 節見出し、外部DHCPv6サーバーについての導入文、Procedure、Step 1〜14の順序を確認した。
- 各手順にはCommand or Action、Example、Purposeがそれぞれ存在する。
- Step 1の`Enter your password if prompted.`以外に、この手順範囲の独立した
  Note・Warning・条件注記は見つからなかった。
- ページ39末尾からページ40冒頭へ続くのはStep 9の例である。
  Step 14の後にある別節「Verifying IPv6 Address Learning Configuration」は含めていない。
- 今回の照合による候補本文の修正は不要と判断した。未解決なのは適用承認、
  原本の記述自体の技術的妥当性、他の監査指摘、下流の内容保持である。
