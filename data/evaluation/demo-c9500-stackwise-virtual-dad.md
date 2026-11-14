# Cisco RAG生成レポート

## 質問

Catalyst 9500のFortyGigabitEthernet1/0/3をStackWise VirtualのFast Hello Dual-Active-Detectionリンクとして設定してください。

## 生成コンフィグ

```text
interface FortyGigabitEthernet1/0/3
 stackwise-virtual dual-active-detection
```

## 説明

FortyGigabitEthernet1/0/3 を StackWise Virtual Fast Hello Dual-Active-Detection (DAD) リンクとして設定するには、根拠の例示どおりインターフェイスコンフィグモードで `stackwise-virtual dual-active-detection` を投入します。[catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0111] のサンプルでは、Switch 1/2 の FortyGigabitEthernet1/0/3 に対して同コマンドを適用して DAD リンクとして動作させています。

なお、根拠では `configure terminal` や `write memory`、`reload` などの手順も示されていますが、要件ではインターフェイスの設定のみが求められているため、それらのコマンドは含めていません。

## 注意事項

- この設定例は Switch 1/2 双方に対して FortyGigabitEthernet1/0/3 を DAD リンクとして設定する構成を示していますが、要件からはどちらのスイッチ側かが明示されていません。
- 根拠では StackWise Virtual 構成の反映に reload が必要である旨が記載されていますが、要件に reload や保存操作の実施有無が指定されていないため、本回答の configuration には含めていません。

## 参照根拠

- [`catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0111`](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md)

## 根拠外の推測

- 知識利用ポリシー: `disallow`
- 推測コマンド数: 0

## 検索結果

| 順位 | スコア | 文書 | 見出し | 引用ID |
|---:|---:|---|---|---|
| 1 | 1.8606 | [catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md) | Verifying Cisco StackWise Virtual Configuration | `catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0116` |
| 2 | 1.7581 | [catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md) | Configuration Examples for StackWise Virtual > Example: Configuring StackWise Virtual Link > Example: Configuring StackWise Virtual Fast Hello Dual-Active-Detection Link | `catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0111` |
| 3 | 1.4052 | [catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md) | Configuration Examples for StackWise Virtual > Example:DisplayingStackWiseVirtualDual-Active-DetectionLinkInformation | `catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0115` |
| 4 | 1.0000 | [catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md) | Configuration Examples for StackWise Virtual > Example: Configuring StackWise Virtual Link > Example: Displaying StackWise Virtual Link Information > Sample output of show stackwise-virtual link command | `catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0114` |
| 5 | 1.0000 | [catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843/manual.md) | Configuration Examples for Interface Characteristics > Example: Configuring 40G and 100G on C9500-32QC | `catalyst9500-26-x-b-26x-int-and-hw-9500-cg-cb55fa160843:section-0139` |
| 6 | 0.7569 | [catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271](../products/catalyst9500/packages/catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271/manual.md) | Configure Cisco StackWise Virtual > Configuring BUM Traffic Optimization > Configuring StackWise Virtual Fast Hello Dual-Active-Detection Link | `catalyst9500-26-x-b-26x-ha-9500-cg-cae50f564271:section-0094` |
