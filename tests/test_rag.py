from pathlib import Path
import hashlib
import json
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models

from scripts.evaluate_rag import evaluate_case
from scripts.rag import (
    RAGError,
    SearchFeature,
    SearchResult,
    _contains_search_phrase,
    _contains_search_concept,
    _contains_search_terms_in_order,
    _supports_configuration_term,
    _LexicalCorpus,
    _search_lexical_corpus,
    _select_feature_balanced,
    expand_search_plan,
    generate_configuration,
    format_markdown_report,
    migrate_product_collections,
    _prioritize_cli_terms,
    product_collection_name,
    search_index,
    search_expanded,
    search_lexical,
    search_manuals,
    section_point_id,
    split_markdown_sections,
    build_index,
    update_index_incrementally,
)
from scripts.transform_markdown import LLMConfig


def test_split_markdown_sections_keeps_heading_context() -> None:
    markdown = (
        "---\n{}\n---\n\n"
        "# Guide\n\nIntro\n\n"
        "## Configure DHCP\n\nip helper-address 192.0.2.1\n"
    )

    sections = split_markdown_sections(
        markdown, "guide", "Guide", "26", max_characters=500
    )

    assert sections[-1].citation_id == "guide:section-0001"
    assert sections[-1].heading_path == ("Guide", "Configure DHCP")
    assert "ip helper-address" in sections[-1].text


def test_split_markdown_sections_rejects_small_limit() -> None:
    with pytest.raises(RAGError, match="at least 500"):
        split_markdown_sections("# Guide\n", "guide", "Guide", "26", 100)


def test_procedure_headings_keep_their_owner_without_leaking_to_next_section() -> None:
    sections = split_markdown_sections(
        "# Guide\n\n## VLAN\n\n### Creating a VLAN\n\n"
        "### Before you begin\n\nvlan vlan-id\n\n"
        "### Procedure\n\nvlan 20\n\n"
        "### Deleting a VLAN\n\n### Before you begin\n\nno vlan 20\n",
        "guide", "Guide", "26", max_characters=500,
    )
    before = next(item for item in sections if "vlan vlan-id" in item.text)
    procedure = next(item for item in sections if item.heading_path[-1] == "Procedure")
    deletion = next(item for item in sections if "no vlan 20" in item.text)
    assert before.heading_path[-2:] == ("Creating a VLAN", "Before you begin")
    assert procedure.heading_path[-2:] == ("Creating a VLAN", "Procedure")
    assert deletion.heading_path[-2:] == ("Deleting a VLAN", "Before you begin")
    assert "Creating a VLAN" not in deletion.heading_path


def test_generic_heading_does_not_inherit_unrelated_sibling() -> None:
    sections = split_markdown_sections(
        "# Guide\n\n## VLAN\n\n### Overview\n\nText\n\n"
        "### Before you begin\n\nRequirements\n",
        "guide", "Guide", "26", max_characters=500,
    )
    assert sections[-1].heading_path == ("Guide", "VLAN", "Before you begin")


def test_section_point_id_is_stable_and_document_scoped() -> None:
    section = split_markdown_sections(
        "# Guide\n\n## DHCP\n\nip helper-address\n",
        "guide",
        "Guide",
        "26",
        max_characters=500,
    )[0]
    assert section_point_id(section) == section_point_id(section)
    assert len(section_point_id(section)) == 36


class FakeEmbedder:
    dimension = 2
    model_name = "fake"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    def encode_queries(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def write_rag_package(
    packages_dir: Path,
    document_id: str,
    body: str,
    *,
    title: str | None = None,
) -> None:
    package_dir = packages_dir / document_id
    package_dir.mkdir(parents=True, exist_ok=True)
    manual = f"---\n{{}}\n---\n\n# {document_id}\n\n## DHCP\n\n{body}\n"
    manual_path = package_dir / "manual.md"
    manual_path.write_text(manual, encoding="utf-8")
    descriptor = {
        "profile": "data-package",
        "name": document_id,
        "title": title or document_id,
        "version": "26",
        "configbrain": {
            "catalogProduct": (
                "catalyst9500" if "9500" in document_id else "catalyst9300"
            ),
            "release": "26.x",
        },
        "resources": [
            {
                "name": "manual",
                "path": "manual.md",
                "mediatype": "text/markdown",
                "bytes": manual_path.stat().st_size,
                "hash": f"sha256:{hashlib.sha256(manual_path.read_bytes()).hexdigest()}",
            }
        ],
    }
    (package_dir / "datapackage.json").write_text(json.dumps(descriptor), encoding="utf-8")


def test_incremental_index_updates_only_changed_package(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    write_rag_package(packages, "guide-a", "ip helper-address 192.0.2.1")
    write_rag_package(packages, "guide-b", "ntp server 192.0.2.1")
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()

    build_index(packages, qdrant_path, embedder)
    embedder.calls.clear()
    updated = update_index_incrementally(packages, qdrant_path, embedder)
    assert updated["incremental"] == {"updated": [], "removed": []}
    assert embedder.calls == []

    write_rag_package(packages, "guide-b", "ntp server 192.0.2.2")
    updated = update_index_incrementally(packages, qdrant_path, embedder)
    assert updated["incremental"] == {"updated": ["guide-b"], "removed": []}
    assert len(embedder.calls) == 1
    assert "192.0.2.2" in embedder.calls[0][0]


def test_incremental_index_rebuilds_old_section_version(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    write_rag_package(packages, "guide-a", "ip helper-address 192.0.2.1")
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(packages, qdrant_path, embedder)
    manifest_path = qdrant_path / "index-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("section_versions")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    embedder.calls.clear()

    updated = update_index_incrementally(packages, qdrant_path, embedder)

    assert updated["incremental"]["updated"] == ["guide-a"]
    assert updated["section_versions"]["catalyst9300"] == 2
    assert embedder.calls
    embedder.calls.clear()
    update_index_incrementally(packages, qdrant_path, embedder)
    assert embedder.calls == []


def test_index_requires_normalized_catalog_product(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    write_rag_package(packages, "guide-a", "ip helper-address 192.0.2.1")
    descriptor_path = packages / "guide-a" / "datapackage.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["configbrain"].pop("catalogProduct")
    descriptor["configbrain"]["product"] = "Cisco Catalyst 9300 Series Switches"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    with pytest.raises(RAGError, match="normalized catalog product metadata"):
        build_index(packages, tmp_path / "qdrant", FakeEmbedder())


def test_incremental_index_deletes_only_current_product_documents(
    tmp_path: Path,
) -> None:
    initial_packages = tmp_path / "initial-packages"
    write_rag_package(initial_packages, "guide-9300", "platform command 9300")
    write_rag_package(initial_packages, "guide-9500-a", "platform command 9500 a")
    write_rag_package(initial_packages, "guide-9500-b", "platform command 9500 b")
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(initial_packages, qdrant_path, embedder)

    catalyst9500_packages = tmp_path / "catalyst9500-packages"
    write_rag_package(
        catalyst9500_packages,
        "guide-9500-a",
        "updated platform command 9500 a",
    )
    updated = update_index_incrementally(
        catalyst9500_packages,
        qdrant_path,
        embedder,
    )

    assert updated["documents"] == 2
    assert updated["incremental"] == {
        "updated": ["guide-9500-a"],
        "removed": ["guide-9500-b"],
    }
    assert updated["source_products"] == {
        "guide-9300": "catalyst9300",
        "guide-9500-a": "catalyst9500",
    }
    assert updated["collections"]["catalyst9300"]["documents"] == 1
    assert updated["collections"]["catalyst9500"]["documents"] == 1
    assert {
        result.document_id
        for result in search_lexical(
            "platform command",
            qdrant_path,
            limit=10,
            product="catalyst9300",
        )
    } == {"guide-9300"}
    assert {
        result.document_id
        for result in search_lexical(
            "platform command",
            qdrant_path,
            limit=10,
            product="catalyst9500",
        )
    } == {"guide-9500-a"}


def test_migrate_shared_collection_splits_products_without_reembedding(
    tmp_path: Path,
) -> None:
    qdrant_path = tmp_path / "qdrant"
    qdrant_path.mkdir()
    collection = "cisco_manual_sections"
    client = QdrantClient(path=str(qdrant_path))
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(
                id=1,
                vector=[1.0, 0.0],
                payload={"document_id": "guide-9300", "product": "catalyst9300"},
            ),
            models.PointStruct(
                id=2,
                vector=[0.0, 1.0],
                payload={"document_id": "guide-9500", "product": "catalyst9500"},
            ),
        ],
        wait=True,
    )
    client.close()
    (qdrant_path / "index-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "collection": collection,
                "embedding_model": "fake",
                "vector_size": 2,
                "documents": 2,
                "sections": 2,
                "source_sha256": {
                    "guide-9300": "a",
                    "guide-9500": "b",
                },
                "source_products": {
                    "guide-9300": "catalyst9300",
                    "guide-9500": "catalyst9500",
                },
            }
        ),
        encoding="utf-8",
    )

    migrated = migrate_product_collections(qdrant_path)

    assert migrated["schema_version"] == 2
    assert migrated["sections"] == 2
    client = QdrantClient(path=str(qdrant_path))
    assert not client.collection_exists(collection)
    assert client.count(
        collection_name=product_collection_name("catalyst9300"),
        exact=True,
    ).count == 1
    assert client.count(
        collection_name=product_collection_name("catalyst9500"),
        exact=True,
    ).count == 1
    client.close()


def test_evaluate_case_checks_retrieval_and_configuration() -> None:
    case = {
        "id": "dhcp",
        "retrieval_terms": ["ip helper-address"],
        "required_patterns": [r"(?m)^ip helper-address 192\.0\.2\.50$"],
        "forbidden_patterns": [r"(?m)^no ip helper-address"],
        "product": "catalyst9300",
        "release": "26.x",
    }
    generated = {
        "configuration": "interface Vlan100\n ip helper-address 192.0.2.50\n",
        "citations": ["guide:section-0001"],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "product": "catalyst9300",
                "release": "26.x",
            }
        ],
    }

    result = evaluate_case(case, "Use ip helper-address on the interface.", generated)

    assert result["retrieval_pass"]
    assert result["configuration_pass"]
    assert result["citation_pass"]
    assert result["product_isolation_pass"]


def test_evaluate_case_rejects_operational_commands_in_configuration() -> None:
    case = {
        "id": "vtp",
        "retrieval_terms": ["vtp mode transparent"],
        "required_patterns": [r"(?m)^vtp mode transparent$"],
        "forbidden_patterns": [],
        "product": "catalyst9300",
        "release": "26.x",
    }
    generated = {
        "configuration": "show vtp status\nvtp mode transparent\n",
        "citations": ["guide:section-0001"],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "product": "catalyst9300",
                "release": "26.x",
            }
        ],
    }

    result = evaluate_case(
        case,
        "Use vtp mode transparent.",
        generated,
    )

    assert not result["configuration_pass"]
    assert result["operational_command_checks"] == {
        "show vtp status": False,
        "vtp mode transparent": True,
    }


def test_evaluate_case_checks_expected_configuration_refusal() -> None:
    case = {
        "id": "unsupported",
        "retrieval_terms": [],
        "required_patterns": [],
        "forbidden_patterns": [],
        "product": "catalyst9300",
        "release": "26.x",
        "expect_no_configuration": True,
    }
    generated = {
        "configuration": "",
        "citations": ["guide:section-0001"],
        "warnings": ["取得した根拠では設定を確認できません。"],
        "inferred_commands": [],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "product": "catalyst9300",
                "release": "26.x",
            }
        ],
    }

    result = evaluate_case(case, "", generated)
    assert result["configuration_pass"]
    assert result["warning_pass"]
    assert result["inference_pass"]

    generated["configuration"] = "unsupported command"
    result = evaluate_case(case, "", generated)
    assert not result["configuration_pass"]

    generated["configuration"] = ""
    generated["warnings"] = []
    result = evaluate_case(case, "", generated)
    assert not result["configuration_pass"]
    assert not result["warning_pass"]


def test_evaluate_case_matches_bracketed_no_syntax() -> None:
    case = {
        "id": "remove-ntp",
        "retrieval_terms": ["no ntp server"],
        "required_patterns": [r"(?m)^no ntp server 192\.0\.2\.123$"],
        "forbidden_patterns": [],
        "product": "catalyst9300",
        "release": "26.x",
    }
    generated = {
        "configuration": "no ntp server 192.0.2.123\n",
        "citations": ["guide:section-0001"],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "product": "catalyst9300",
                "release": "26.x",
            }
        ],
    }

    result = evaluate_case(
        case,
        "[ no ] ntp server ip-address [ prefer ]",
        generated,
    )

    assert result["retrieval_pass"]
    assert result["configuration_pass"]


def test_evaluate_case_matches_ordered_cli_with_optional_tokens() -> None:
    case = {
        "id": "remove-ntp",
        "retrieval_terms": ["no ntp server"],
        "required_patterns": [],
        "forbidden_patterns": [],
        "product": "catalyst9300",
        "release": "26.x",
        "expect_no_configuration": True,
    }
    generated = {
        "configuration": "",
        "citations": ["guide:section-0001"],
        "warnings": ["削除対象が不足しています。"],
        "inferred_commands": [],
        "retrieval": [
            {
                "citation_id": "guide:section-0001",
                "product": "catalyst9300",
                "release": "26.x",
            }
        ],
    }

    result = evaluate_case(
        case,
        "Use no form of the command: no [vrf vrf-name] ntp server ip-address.",
        generated,
    )

    assert result["retrieval_pass"]


def test_search_filters_mixed_product_index(tmp_path: Path) -> None:
    packages = tmp_path / "packages"
    write_rag_package(packages, "guide-9300", "platform command 9300")
    write_rag_package(packages, "guide-9500", "platform command 9500")
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(packages, qdrant_path, embedder)

    vector_results = search_index(
        "platform command",
        qdrant_path,
        embedder,
        limit=10,
        product="catalyst9500",
        release="26.x",
    )
    lexical_results = search_lexical(
        "platform command",
        qdrant_path,
        limit=10,
        product="catalyst9500",
        release="26.x",
    )

    assert {item.product for item in vector_results} == {"catalyst9500"}
    assert {item.product for item in lexical_results} == {"catalyst9500"}


def test_search_expanded_loads_lexical_corpus_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages = tmp_path / "packages"
    write_rag_package(
        packages,
        "guide-9300",
        "Configure an interface with ip helper-address.",
    )
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(packages, qdrant_path, embedder)

    from scripts import rag

    original_loader = rag._load_lexical_corpus
    load_calls = 0

    def counting_loader(*args: object, **kwargs: object) -> object:
        nonlocal load_calls
        load_calls += 1
        return original_loader(*args, **kwargs)

    monkeypatch.setattr(rag, "_load_lexical_corpus", counting_loader)

    results = search_expanded(
        ["DHCP relay", "configure helper address"],
        qdrant_path,
        embedder,
        cli_terms=["ip helper-address"],
        product="catalyst9300",
        release="26.x",
    )

    assert load_calls == 1
    assert results
    assert results[0].product == "catalyst9300"


def test_select_feature_balanced_reserves_evidence_per_feature() -> None:
    def result(citation_id: str) -> SearchResult:
        return SearchResult(
            citation_id=citation_id,
            score=0.0,
            document_id=citation_id,
            title=citation_id,
            version="26",
            product="catalyst9300",
            release="26.x",
            heading_path=(),
            text=citation_id,
        )

    ntp_primary = result("ntp-primary")
    ntp_secondary = result("ntp-secondary")
    syslog = result("syslog")
    ranked = {
        "ntp-primary": (ntp_primary, 3.0),
        "ntp-secondary": (ntp_secondary, 2.0),
        "syslog": (syslog, 1.0),
    }

    selected = _select_feature_balanced(
        ranked,
        [
            {
                "ntp-primary": (ntp_primary, 2.0),
                "ntp-secondary": (ntp_secondary, 1.0),
            },
            {"syslog": (syslog, 1.0)},
        ],
        limit=2,
    )

    assert [item.citation_id for item, _ in selected] == [
        "ntp-primary",
        "syslog",
    ]


def test_expand_search_plan_returns_atomic_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["configure NTP client server"],
                    "cli_terms": ["ntp server"],
                },
                {
                    "queries": ["configure remote syslog host"],
                    "cli_terms": ["logging host"],
                },
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    queries, cli_terms, features = expand_search_plan(
        "NTPとSyslogを設定",
        config,
    )

    assert queries == [
        "NTPとSyslogを設定",
        "configure NTP client server",
        "configure remote syslog host",
    ]
    assert cli_terms == ["ntp server", "logging host"]
    assert features == [
        SearchFeature(("configure NTP client server",), ("ntp server",)),
        SearchFeature(("configure remote syslog host",), ("logging host",)),
    ]


def test_expand_search_plan_corrects_vlan_name_cli_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["configure VLAN name"],
                    "cli_terms": ["vlan", "vlan name"],
                }
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    _, cli_terms, features = expand_search_plan(
        "VLAN 100を作成し、名前をUSERSに設定してください。",
        config,
    )

    assert cli_terms == ["name vlan-name", "vlan vlan-id", "vlan", "vlan name"]
    assert features[0].cli_terms == tuple(cli_terms)


def test_expand_search_plan_normalizes_values_and_adds_svi_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {"features": [{
            "queries": ["configure SVI"],
            "cli_terms": [
                "interface Vlan120", "ip address 192.0.2.1 255.255.255.0",
            ],
        }]},
    )
    config = LLMConfig(
        api_url="https://example.test", api_key="key", model="model",
        api_key_header="Authorization",
    )
    queries, terms, _ = expand_search_plan(
        "SVI Vlan120のIPアドレスは192.0.2.1/24としてください。", config
    )
    assert "interface vlan vlan-id" in terms
    assert "ip address ip-address subnet-mask" in terms
    assert all("192.0.2.1" not in term and "Vlan120" not in term for term in terms)
    assert any("manually assigning ip information" in query for query in queries)


def test_cli_candidates_are_reranked_before_limiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def candidate(name: str, heading: str) -> SearchResult:
        return SearchResult(
            citation_id=name, score=1.0, document_id=name, title="VLAN guide",
            version="26", product="catalyst9300", release="26.x",
            heading_path=(heading,), text="vlan vlan-id",
        )
    candidates = [
        candidate(f"other-{index}", "Other VLAN usage") for index in range(3)
    ] + [candidate("standard", "Creating or Modifying an Ethernet VLAN")]
    corpus = SimpleNamespace(points=tuple(range(4)))
    monkeypatch.setattr("scripts.rag._load_lexical_corpus", lambda *args, **kwargs: corpus)
    monkeypatch.setattr(
        "scripts.rag._search_lexical_corpus",
        lambda query, corpus, limit, **kwargs: candidates[:limit],
    )
    feature = SearchFeature(
        ("creating or modifying an ethernet vlan vlan vlan-id",), ("vlan vlan-id",)
    )
    results = search_expanded(
        [], tmp_path, FakeEmbedder(), limit=1, cli_terms=["vlan vlan-id"],
        features=[feature], product="catalyst9300", release="26.x",
    )
    assert results[0].citation_id == "standard"


def test_expand_search_plan_prefers_standard_dhcp_relay_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["configure DHCP relay on an SVI"],
                    "cli_terms": [
                        "ip helper-address",
                        "ip helper-address global",
                    ],
                }
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    queries, cli_terms, features = expand_search_plan(
        "Vlan100のDHCP要求を192.0.2.50へ転送してください",
        config,
    )

    assert (
        "DHCP relay agent specifying the packet forwarding address "
        "ip helper-address address"
    ) in queries
    assert cli_terms[:2] == [
        "ip helper-address address",
        "ip helper-address",
    ]
    assert "ip helper-address global" not in cli_terms
    assert "EVPN" in features[0].excluded_terms
    assert "tenant VRF" in features[0].excluded_terms


def test_expand_search_plan_keeps_explicit_tenant_vrf_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["configure DHCP relay between tenant and default VRFs"],
                    "cli_terms": ["ip helper-address global"],
                }
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    _, cli_terms, features = expand_search_plan(
        "DHCPクライアントはテナントVRF blue、DHCPサーバーはデフォルトVRFです。",
        config,
    )

    assert cli_terms == ["ip helper-address global"]
    assert features[0].excluded_terms == ()


def test_search_expanded_ranks_standard_dhcp_relay_guide_first(
    tmp_path: Path,
) -> None:
    packages = tmp_path / "packages"
    write_rag_package(
        packages,
        "ip-addressing",
        (
            "## Specifying the Packet Forwarding Address\n\n"
            "Use the ip helper-address address interface command on the "
            "Layer 3 interface closest to the DHCP client."
        ),
        title="Catalyst 9300 IP Addressing Services Configuration Guide",
    )
    write_rag_package(
        packages,
        "evpn-guide",
        (
            "## DHCP Relay in a BGP EVPN VXLAN Fabric\n\n"
            "The DHCP client is in a tenant VRF and the server is in the "
            "default VRF. Configure ip helper-address global 192.0.2.50."
        ),
        title="Catalyst 9300 BGP EVPN VXLAN Configuration Guide",
    )
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(packages, qdrant_path, embedder)

    query = (
        "DHCP relay agent specifying the packet forwarding address "
        "ip helper-address address"
    )
    feature = SearchFeature(
        (query,),
        ("ip helper-address address", "ip helper-address"),
        (
            "EVPN",
            "VXLAN",
            "tenant VRF",
            "ip helper-address global",
            "ip helper-address vrf",
        ),
    )
    results = search_expanded(
        [query],
        qdrant_path,
        embedder,
        limit=6,
        cli_terms=list(feature.cli_terms),
        features=[feature],
        product="catalyst9300",
        release="26.x",
    )

    assert results[0].document_id == "ip-addressing"
    assert results[0].title == (
        "Catalyst 9300 IP Addressing Services Configuration Guide"
    )


def test_prioritize_cli_terms_caps_expansion_without_dropping_features() -> None:
    features = [
        SearchFeature(
            queries=("configure NTP",),
            cli_terms=tuple(f"ntp option-{index}" for index in range(20)),
        ),
        SearchFeature(
            queries=("configure Syslog",),
            cli_terms=tuple(f"logging option-{index}" for index in range(20)),
        ),
    ]

    prioritized, terms = _prioritize_cli_terms(
        features,
        "NTPとSyslogを設定",
    )

    assert len(terms) == 24
    assert terms[:2] == ["ntp server", "logging host"]
    assert prioritized[0].cli_terms
    assert prioritized[1].cli_terms


def test_expand_search_plan_adds_no_form_for_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["remove configured NTP server"],
                    "cli_terms": ["ntp server", "show ntp associations"],
                }
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    _, cli_terms, features = expand_search_plan(
        "NTPサーバー設定を削除してください",
        config,
    )

    assert cli_terms == [
        "no ntp server",
        "ntp server",
    ]
    assert features[0].cli_terms == tuple(cli_terms)


def test_contains_search_phrase_normalizes_bracketed_no_syntax() -> None:
    assert _contains_search_phrase(
        "[ no ] ntp server ip-address [ prefer ]",
        "no ntp server",
    )


def test_ordered_search_restarts_after_an_early_distant_prefix() -> None:
    text = "switchport " + "filler " * 20 + "switchport mode trunk"

    assert _contains_search_terms_in_order(text, "switchport mode trunk")


def test_expand_search_plan_excludes_authentication_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, config: {
            "features": [
                {
                    "queries": ["configure NTP server without authentication"],
                    "cli_terms": [
                        "ntp server",
                        "ntp authenticate",
                        "ntp trusted-key",
                    ],
                    "excluded_terms": ["authentication"],
                }
            ]
        },
    )
    config = LLMConfig(
        api_url="https://example.test",
        api_key="key",
        model="model",
        api_key_header="Authorization",
    )

    _, cli_terms, features = expand_search_plan(
        "認証を使用せずNTPサーバーを設定してください",
        config,
    )

    assert cli_terms == ["ntp server"]
    assert features[0].excluded_terms == (
        "authentication",
        "auth",
        "key",
        "trusted-key",
    )


def test_contains_search_concept_matches_word_stem() -> None:
    assert _contains_search_concept("Configuring NTP Authentication", "auth")
    assert _contains_search_concept("ntp authenticate", "auth")


def test_contains_search_terms_in_order_allows_cli_arguments() -> None:
    syntax = (
        "[ no ] ntp server [ vrf vrf-name ] ip-address "
        "[ version number ] [ key key-id ] [ source interface ] [ prefer ]"
    )

    assert _contains_search_terms_in_order(syntax, "ntp server prefer")
    assert not _contains_search_terms_in_order(syntax, "ntp prefer server")


def test_configuration_term_support_excludes_show_only_evidence() -> None:
    assert _supports_configuration_term(
        "Device(config-vlan)# name test20\nname vlan-name",
        "name vlan-name",
    )
    assert not _supports_configuration_term(
        "Device# show vlan name test20\n"
        "show vlan { name vlan-name | id vlan-id }",
        "name vlan-name",
    )


def test_search_lexical_corpus_filters_ordered_terms_before_limiting() -> None:
    def point(citation_id: str, text: str) -> SimpleNamespace:
        return SimpleNamespace(payload={
            "citation_id": citation_id,
            "document_id": citation_id,
            "title": citation_id,
            "version": "26",
            "product": "catalyst9300",
            "release": "26.x",
            "heading_path": [],
            "text": text,
        })

    corpus = _LexicalCorpus(
        points=(
            point("higher-non-match", "ntp server ntp server example"),
            point("ordered-match", "[ no ] ntp server ip-address"),
        ),
        documents=(
            ("ntp", "server", "ntp", "server", "example"),
            ("no", "ntp", "server", "ip-address"),
        ),
        document_frequencies={
            "no": 1,
            "ntp": 2,
            "server": 2,
            "example": 1,
            "ip-address": 1,
        },
        average_length=4.5,
    )

    results = _search_lexical_corpus(
        "no ntp server",
        corpus,
        limit=3,
        require_terms_in_order=True,
    )

    assert [item.citation_id for item in results] == ["ordered-match"]


def test_search_manuals_crosses_products_and_deduplicates_sections(
    tmp_path: Path,
) -> None:
    packages = tmp_path / "packages"
    write_rag_package(packages, "guide-9300", "platform command 9300")
    write_rag_package(packages, "guide-9500", "platform command 9500")
    qdrant_path = tmp_path / "qdrant"
    embedder = FakeEmbedder()
    build_index(packages, qdrant_path, embedder)

    results = search_manuals(
        "platform command",
        qdrant_path,
        embedder,
        limit=10,
    )

    assert {item.product for item in results} == {"catalyst9300", "catalyst9500"}
    assert len({item.citation_id for item in results}) == len(results)


def test_format_markdown_report_is_readable_and_escapes_table_cells() -> None:
    report = format_markdown_report(
        {
            "requirement": "VLAN 120を設定してください。",
            "knowledge_policy": "disallow",
            "configuration": (
                "interface GigabitEthernet1/0/20\n"
                " switchport access vlan 120\n"
            ),
            "explanation": "アクセスポートとして設定します。",
            "citations": ["guide:section-0001"],
            "warnings": ["VLAN自体の作成は含みません。"],
            "missing_required_values": ["SVIのIPアドレス"],
            "unsupported_requirements": ["必要な根拠"],
            "inferred_commands": [],
            "retrieval": [
                {
                    "citation_id": "guide:section-0001",
                    "score": 1.25,
                    "document_id": "guide",
                    "heading_path": ["VLAN", "Access | ports"],
                }
            ],
        },
        Path("data/evaluation/report.md"),
    )

    assert report.startswith("# Cisco RAG生成レポート\n")
    assert "```text\ninterface GigabitEthernet1/0/20" in report
    assert "- 推測コマンド数: 0" in report
    assert "## 不足値\n\n- SVIのIPアドレス" in report
    assert "## 未対応要件\n\n- 必要な根拠" in report
    assert (
        "[`guide:section-0001`](../products/catalyst9300/packages/guide/manual.md)"
        in report
    )
    assert "[guide](../products/catalyst9300/packages/guide/manual.md)" in report
    assert "VLAN > Access \\| ports" in report


def generation_inputs() -> tuple[list[SearchResult], LLMConfig]:
    results = [
        SearchResult(
            citation_id="guide:section-0001",
            score=1.0,
            document_id="guide",
            title="Guide",
            version="26",
            product="catalyst9300",
            release="26.x",
            heading_path=("NTP",),
            text="Use the ntp server ip-address command.",
        )
    ]
    config = LLMConfig(
        api_url="https://example.invalid",
        api_key="test",
        model="test",
        api_key_header="Authorization",
    )
    return results, config


def test_generate_configuration_disallows_prior_knowledge_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "ntp server 192.0.2.1",
            "explanation": "事前知識で補完しました。",
            "citations": ["guide:section-0001"],
            "warnings": [],
            "inferred_commands": ["ntp server 192.0.2.1"],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    with pytest.raises(RAGError, match="disallowed prior knowledge"):
        generate_configuration("NTPを設定", results, config)


def test_generate_configuration_maps_short_evidence_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "ntp server 192.0.2.1",
            "explanation": ["根拠[E1]を使用しました。", "追加説明です。"],
            "citations": ["E1"],
            "warnings": ["注意事項[E1]"],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration("NTPを設定", results, config)

    assert generated["citations"] == ["guide:section-0001"]
    assert "[guide:section-0001]" in generated["explanation"]
    assert "\n\n追加説明です。" in generated["explanation"]
    assert generated["warnings"] == ["注意事項[guide:section-0001]"]


def test_generate_configuration_moves_requested_example_to_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    results[0] = SearchResult(
        **{
            **results[0].__dict__,
            "source_url": "https://www.cisco.com/example",
        }
    )
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "router bgp 65000\n"
                " address-family l2vpn evpn"
            ),
            "explanation": "根拠に記載されたEVPN設定例です。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    result = generate_configuration(
        "EVPNの設定例を提示してください",
        results,
        config,
    )

    assert result["configuration"] == ""
    assert "参考設定例（そのまま投入不可）" in result["explanation"]
    assert "router bgp 65000" in result["explanation"]
    assert result["warnings"] == [
        "設定例の提示要求のため、参考例は説明に記載し、"
        "実投入用コンフィグは生成していません。"
    ]
    assert result["retrieval"][0]["source_url"] == (
        "https://www.cisco.com/example"
    )


def test_generate_configuration_allows_declared_limited_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "ntp server 192.0.2.1",
            "explanation": "構文を補完しました。",
            "citations": ["guide:section-0001"],
            "warnings": ["CLI構文を事前知識で補完しました。"],
            "inferred_commands": ["ntp server 192.0.2.1"],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "NTPを設定", results, config, knowledge_policy="limited"
    )

    assert generated["knowledge_policy"] == "limited"
    assert generated["inferred_commands"] == ["ntp server 192.0.2.1"]


def test_generate_configuration_requires_inference_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "ntp server 192.0.2.1",
            "explanation": "設定しました。",
            "citations": ["guide:section-0001"],
            "warnings": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    with pytest.raises(RAGError, match="invalid structure"):
        generate_configuration("NTPを設定", results, config)


def test_generate_configuration_discards_partial_unsupported_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "interface GigabitEthernet1/0/3",
            "explanation": "主要機能の根拠が不足しています。",
            "citations": ["E1"],
            "warnings": ["StackWise Virtual固有コマンドを確認できません。"],
            "inferred_commands": [],
            "unsupported_requirements": [
                "StackWise Virtual Dual-Active-Detection"
            ],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration("StackWise Virtualを設定", results, config)

    assert generated["configuration"] == ""
    assert generated["inferred_commands"] == []
    assert generated["unsupported_requirements"] == [
        "StackWise Virtual Dual-Active-Detection"
    ]
    assert any("部分設定を破棄" in warning for warning in generated["warnings"])
    assert any(
        "interface GigabitEthernet1/0/3" in warning
        for warning in generated["warnings"]
    )


def test_generate_configuration_discards_partial_when_required_value_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "logging on",
            "explanation": "Syslog送信先が不足しています。",
            "citations": ["E1"],
            "warnings": ["Syslogサーバーを指定できません。"],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": ["SyslogサーバーのIPアドレス"],
        },
    )

    generated = generate_configuration("Syslogを設定", results, config)

    assert generated["configuration"] == ""
    assert generated["missing_required_values"] == [
        "SyslogサーバーのIPアドレスまたはホスト名"
    ]
    assert any("生成前の要件検証" in warning for warning in generated["warnings"])


def test_generate_configuration_preflights_missing_syslog_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()

    def fail_if_called(messages: list[dict[str, str]], llm_config: LLMConfig):
        raise AssertionError("LLM generation must be skipped")

    monkeypatch.setattr("scripts.rag._chat_json", fail_if_called)

    generated = generate_configuration("外部Syslogサーバーへログを送信", results, config)

    assert generated["configuration"] == ""
    assert generated["missing_required_values"] == [
        "SyslogサーバーのIPアドレスまたはホスト名"
    ]
    assert generated["inferred_commands"] == []


def test_generate_configuration_preflights_missing_svi_address_for_new_vlan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()

    def fail_if_called(messages: list[dict[str, str]], llm_config: LLMConfig):
        raise AssertionError("LLM generation must be skipped")

    monkeypatch.setattr("scripts.rag._chat_json", fail_if_called)

    generated = generate_configuration(
        "VLAN 120を作成し、Vlan120で受信するDHCP要求を"
        "DHCPサーバー192.0.2.50へ転送してください。",
        results,
        config,
    )

    assert generated["configuration"] == ""
    assert generated["missing_required_values"] == [
        "Vlan120のIPv4アドレスとプレフィックス"
    ]
    assert generated["unsupported_requirements"] == []
    assert generated["inferred_commands"] == []
    assert any("Vlan120のIPv4アドレスとプレフィックス" in warning for warning in generated["warnings"])


def test_generate_configuration_accepts_explicit_svi_ipv4_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "vlan 120\n"
                "interface Vlan120\n"
                " ip address 192.0.2.1 255.255.255.0\n"
                " ip helper-address 192.0.2.50"
            ),
            "explanation": "SVIのIPアドレスとDHCPリレーを設定します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "VLAN 120を作成し、Vlan120のSVI IPアドレスを192.0.2.1/24に設定し、"
        "DHCPサーバー192.0.2.50へDHCP要求を転送してください。",
        results,
        config,
    )

    assert generated["configuration"].startswith("vlan 120\n")
    assert generated["missing_required_values"] == []
    assert generated["unsupported_requirements"] == []


def test_generate_configuration_keeps_existing_svi_relay_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "interface Vlan100\n"
                " ip helper-address 192.0.2.50"
            ),
            "explanation": "既存SVIにDHCPリレーを設定します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "既存のVlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送してください。",
        results,
        config,
    )

    assert generated["configuration"] == (
        "interface Vlan100\n ip helper-address 192.0.2.50\n"
    )
    assert generated["missing_required_values"] == []
    assert generated["unsupported_requirements"] == []


def test_generate_configuration_preflights_missing_ntp_delete_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()

    def fail_if_called(messages: list[dict[str, str]], llm_config: LLMConfig):
        raise AssertionError("LLM generation must be skipped")

    monkeypatch.setattr("scripts.rag._chat_json", fail_if_called)

    generated = generate_configuration("設定済みのNTPサーバーを削除", results, config)

    assert generated["configuration"] == ""
    assert generated["missing_required_values"] == [
        "削除対象のNTPサーバーIPアドレスまたはホスト名"
    ]
    assert generated["inferred_commands"] == []


def test_generate_configuration_rejects_unrequested_dhcp_global_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "interface Vlan100\n"
                " ip helper-address global 192.0.2.50"
            ),
            "explanation": "DHCPリレーを設定します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "Vlan100のDHCP要求を192.0.2.50へ転送してください。VRF情報は未指定です。",
        results,
        config,
    )

    assert generated["configuration"] == ""
    assert generated["unsupported_requirements"] == [
        "DHCPリレーのVRF専用キーワードが要件にありません"
    ]
    assert any(
        "VRF専用構文を拒否しました" in warning
        for warning in generated["warnings"]
    )


def test_generate_configuration_allows_explicit_cross_vrf_dhcp_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "interface Vlan100\n"
                " ip helper-address global 192.0.2.50"
            ),
            "explanation": "テナントVRFからデフォルトVRFへ中継します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "Vlan100のDHCPクライアントはテナントVRF blue、"
        "DHCPサーバー192.0.2.50はデフォルトVRFにあります。",
        results,
        config,
    )

    assert "ip helper-address global 192.0.2.50" in generated["configuration"]
    assert generated["unsupported_requirements"] == []


def test_generate_configuration_discards_all_for_conflicting_interface_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "ntp server 192.0.2.123\n"
                "interface GigabitEthernet1/0/20\n"
                " switchport mode access\n"
                " switchport mode trunk"
            ),
            "explanation": "要件どおり両方を設定しました。",
            "citations": ["E1"],
            "warnings": ["accessとtrunkの同時指定は矛盾します。"],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "NTPサーバー192.0.2.123を設定し、同時にGigabitEthernet1/0/20を"
        "アクセスポートかつトランクポートとして設定してください。",
        results,
        config,
    )

    assert generated["configuration"] == ""
    assert generated["unsupported_requirements"] == [
        "gigabitethernet1/0/20へのaccessモードとtrunkモードの同時指定"
    ]
    assert any("部分設定を破棄" in warning for warning in generated["warnings"])
    assert any("ntp server 192.0.2.123" in warning for warning in generated["warnings"])


def test_generate_configuration_removes_minimal_syslog_overconfiguration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "enable\n"
                "configure terminal\n"
                "logging on\n"
                "logging trap 3\n"
                "service timestamps log datetime msec year\n"
                "logging host 192.0.2.200\n"
                "end"
            ),
            "explanation": "Syslog送信先を設定しました。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "Syslogサーバー192.0.2.200への最小限の設定を生成してください。",
        results,
        config,
    )

    assert generated["configuration"] == "logging host 192.0.2.200\n"
    assert any(
        "enable, configure terminal, logging on, logging trap 3, "
        "service timestamps log datetime msec year, end" in warning
        for warning in generated["warnings"]
    )


def test_generate_configuration_removes_operational_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "show vtp status\nvtp mode transparent",
            "explanation": "VTPモードを変更します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "VTPのデフォルトがtransparentでなければtransparentにしてください。",
        results,
        config,
    )

    assert generated["configuration"] == "vtp mode transparent\n"
    assert any(
        "show vtp status" in warning
        and "運用・確認コマンドを除外" in warning
        for warning in generated["warnings"]
    )


def test_generate_configuration_normalizes_one_time_minimal_range_macro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "define interface-range access_ports "
                "GigabitEthernet1/0/10 - 12\n"
                "interface range macro access_ports\n"
                " switchport mode access\n"
                " switchport access vlan 100"
            ),
            "explanation": "3ポートをまとめて設定します。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "GigabitEthernet1/0/10から12をVLAN 100のアクセスポートにする"
        "できるだけ短い設定を生成してください。",
        results,
        config,
    )

    assert generated["configuration"] == (
        "interface range GigabitEthernet1/0/10 - 12\n"
        " switchport mode access\n"
        " switchport access vlan 100\n"
    )
    assert any(
        "interface rangeへ正規化" in warning
        for warning in generated["warnings"]
    )


def test_generate_configuration_discards_ungrounded_disallow_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "vlan 100\n name USERS",
            "explanation": "VLANを作成します。",
            "citations": ["E1"],
            "warnings": [
                "根拠内にname構文が記載されていません。"
                "一般的なIOS XE構文を前提として生成しました。E1と[E1]を参照。"
            ],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "VLAN 100を作成し、名前をUSERSにしてください。",
        results,
        config,
    )

    assert generated["configuration"] == ""
    assert generated["unsupported_requirements"] == ["生成CLIの根拠不足"]
    assert any(
        "部分設定を破棄" in warning
        for warning in generated["warnings"]
    )
    assert results[0].citation_id in generated["warnings"][0]
    assert "E1" not in generated["warnings"][0]


def test_generate_configuration_discards_undeclared_limited_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "vlan 100\n name USERS",
            "explanation": "VLANを作成します。",
            "citations": ["E1"],
            "warnings": ["根拠でname構文を確認できないため補完しました。"],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "VLAN 100を作成し、名前をUSERSにしてください。",
        results,
        config,
        knowledge_policy="limited",
    )

    assert generated["configuration"] == ""
    assert generated["unsupported_requirements"] == [
        "根拠外CLIの申告不整合"
    ]


def test_generate_configuration_removes_unrequested_ntp_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": (
                "no ntp authenticate\n"
                "ntp authenticate\n"
                "ntp authentication-key 1 md5 secret\n"
                "ntp server 192.0.2.123 key 1\n"
                "ntp server 192.0.2.124 prefer"
            ),
            "explanation": "NTPを設定しました。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "NTPサーバー192.0.2.123と192.0.2.124を設定し、"
        "192.0.2.124だけを優先サーバーにしてください。認証は使用しません。",
        results,
        config,
    )

    assert generated["configuration"] == (
        "ntp server 192.0.2.123\n"
        "ntp server 192.0.2.124 prefer\n"
    )
    assert any("ntp authenticate" in warning for warning in generated["warnings"])


def test_generate_configuration_builds_grounded_no_auth_ntp_after_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "",
            "explanation": "根拠不足として拒否しました。",
            "citations": ["E1"],
            "warnings": ["認証なし構文を確認できません。"],
            "inferred_commands": [],
            "unsupported_requirements": ["認証なしNTP"],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "認証を使用せずNTPサーバー192.0.2.123を設定してください。",
        results,
        config,
    )

    assert generated["configuration"] == "ntp server 192.0.2.123\n"
    assert generated["unsupported_requirements"] == []


def test_generate_configuration_normalizes_ntp_peer_for_server_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "ntp peer 192.0.2.123",
            "explanation": "NTPを設定しました。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "NTPサーバー192.0.2.123を設定してください。",
        results,
        config,
    )

    assert generated["configuration"] == "ntp server 192.0.2.123\n"
    assert any("ntp peer 192.0.2.123" in warning for warning in generated["warnings"])


def test_generate_configuration_removes_ntp_delete_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, config = generation_inputs()
    monkeypatch.setattr(
        "scripts.rag._chat_json",
        lambda messages, llm_config: {
            "configuration": "no ntp server ip-address",
            "explanation": "削除対象が必要です。",
            "citations": ["E1"],
            "warnings": [],
            "inferred_commands": [],
            "unsupported_requirements": [],
            "missing_required_values": [],
        },
    )

    generated = generate_configuration(
        "NTPサーバー192.0.2.123を削除してください。",
        results,
        config,
    )

    assert generated["configuration"] == ""
    assert any("no ntp server ip-address" in warning for warning in generated["warnings"])


def test_generate_configuration_rejects_mixed_product_evidence() -> None:
    results, config = generation_inputs()
    results.append(
        SearchResult(
            citation_id="other:section-0001",
            score=0.9,
            document_id="other",
            title="Other Guide",
            version="26",
            product="catalyst9500",
            release="26.x",
            heading_path=("NTP",),
            text="Use a platform-specific command.",
        )
    )

    with pytest.raises(RAGError, match="different product"):
        generate_configuration(
            "NTPを設定",
            results,
            config,
            product="catalyst9300",
            release="26.x",
        )
