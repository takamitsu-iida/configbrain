# 複合アクセス設定の安全性デモ

実施日: 2026-11-16

## 目的

Catalyst 9300 / IOS XE 26.xを対象に、VLAN作成、アクセスポート設定、
DHCPリレーをひとつの要件から生成するAPIデモを実施した。
実機への設定投入は行っていない。`knowledge_policy=disallow`を使い、
根拠不足や未指定値がある場合に部分設定を拒否できるかも確認した。

入力に使ったアドレスは文書用の例示アドレスであり、実ネットワークの値ではない。

## 実行方法

FastAPIアプリをインプロセスで起動し、設定生成APIへ2回リクエストした。
生成ジョブのSQLite DBは一時ディレクトリに置き、既存のジョブDBや
フィードバックCSVは変更していない。外部LLM APIを利用した。

### 1回目: 複合要件

- 要件: VLAN 120を作成し、GigabitEthernet1/0/20をアクセスポートにし、
  VLAN 120のDHCP要求を192.0.2.50へ転送する
- 条件: VRFや追加オプションなど、要件にない設定を推測しない
- 検索件数: 10
- 状態: `completed`、処理時間72,400 ms

生成結果:

```text
vlan 120
interface GigabitEthernet1/0/20
 switchport access vlan 120
 switchport mode access
interface Vlan120
 ip helper-address global 192.0.2.50
```

取得根拠は10件すべてCatalyst 9300 / 26.xで、引用IDも検索結果に含まれ、
`inferred_commands`は空だった。一方、DHCPの`global`構文の根拠として引用された
section 0370は、DHCPクライアントがテナントVRF、サーバーがデフォルトVRFに
ある例だった。今回の要件にはVRFがなく、この構文を適用できる根拠にはならない。
したがって、この生成設定は完成デモや投入用設定として採用しない。

### 2回目: 標準SVIの条件を明示

要件を、通常のレイヤー2アクセス構成、VRFなし、DHCPサーバーがデフォルト
ルーティングテーブルから到達可能な構成と明示して再実行した。
検索件数は20。

- 状態: `completed`、処理時間77,334 ms
- `configuration`: 空
- `unsupported_requirements`: `生成CLIの根拠不足`
- `inferred_commands`: 空
- 検索根拠: 20件すべてCatalyst 9300 / 26.x
- 生成結果中の引用ID: すべて検索結果に存在

APIは、VLAN作成構文を明示する根拠が足りないこと、SVIのIPアドレスが
要件にないこと、DHCPリレー手順上の`service dhcp`に注意が必要なことを警告し、
部分設定を破棄した。SVIのアドレス等、必要な運用値を与えずに実投入可能な
設定として見せることはできないため、この拒否は安全側の結果である。

## 結果と次の課題

この試行は、複数機能の複合要件から一見もっともらしい設定が生成されても、
引用された例のトポロジーが対象要件と一致するかをレビューする必要があること、
また不足根拠がある場合にAPIが空設定へ棄権できることを示した。
「成功したコンフィグ生成」のデモとしては未完了であり、1回目の設定は使用しない。

## 実装した改善

デモで見つかった根拠選択と不足値検出に絞り、`scripts/rag.py`を改善した。

- VRFが要件にないDHCP relayでは、通常の`ip helper-address <address>`構文と
  IP Addressing Servicesガイドを検索で優先し、EVPN/VXLANやテナントVRFの
  文脈を減点する
- 明示的なVRF文脈がないのに`ip helper-address global`または`vrf`構文が
  生成された場合は、設定全体を棄権する
- 新規VLAN作成とDHCP relayを同時に要求し、SVIのIPv4アドレス/プレフィックスが
  未指定の場合は、生成前に不足値として返す
- 既存SVIへのDHCP relay追加では、SVIアドレスを不足値として要求しない
- 明示的なテナントVRF要件は、通常構成向けの制限で誤って拒否しない

検証結果:

- `uv run pytest tests/test_rag.py -q`: 56件成功
- `uv run pytest -q`: 168件成功
- `git diff --check`: 成功
- 今回の実装検証では外部LLM APIを呼び出していない

次回は、利用者がSVIのIPアドレスとDHCPサービス状態を確定した要件で再評価し、
通常の`ip helper-address <address>`構文を示す
[Catalyst 9300 IP Addressing Services Configuration Guide](../products/catalyst9300/packages/c9300_iosxe26_ip_addr_svc_cg/manual.md)
が実検索結果で優先されることを確認してから、成功例として扱う。
