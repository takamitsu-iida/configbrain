import json
from app.ingestion.html_loader import HtmlLoader
from app.ingestion.rendered_html_loader import RenderedHtmlFetcher
from app.ingestion.html_blocks import (
  section_to_record,
  write_html_blocks,
  write_llm_candidates,
)
from pathlib import Path


ENTRY_URL = "https://docs.example.test/guides/vlan/book.html"
SECTION_URL = "https://docs.example.test/guides/vlan/book/configuring_vlans.html"


def test_html_loader_crawls_only_sections_of_same_guide() -> None:
    pages = {
        ENTRY_URL: """
        <html><body>
          <h1>VLAN Configuration Guide</h1>
          <div id="bookToc">
            <a href="book/configuring_vlans.html">Configuring VLANs</a>
            <a href="https://evil.example.test/other.html">External</a>
            <a href="../other-guide/other.html">Other guide</a>
          </div>
        </body></html>
        """,
        SECTION_URL: """
        <html><body>
          <h1>VLAN Configuration Guide</h1>
          <div id="pageContentDiv" role="main">
            <h2>Configuring VLANs</h2>
            <p>Create the VLAN before assigning ports.</p>
            <pre><code>Device(config)# vlan 100
Device(config-vlan)# name USERS</code></pre>
            <table><tr><th>Command</th><th>Purpose</th></tr>
              <tr><td>vlan 100</td><td>Create VLAN</td></tr></table>
          </div>
        </body></html>
        """,
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    assert [section.section_url for section in sections] == [SECTION_URL]
    section = sections[0]
    assert section.document_id == "doc-1"
    assert section.section_title == "Configuring VLANs"
    assert "Create the VLAN" in section.text
    assert section.code_blocks == ("Device(config)# vlan 100\nDevice(config-vlan)# name USERS",)
    assert section.tables == ((("Command", "Purpose"), ("vlan 100", "Create VLAN")),)


def test_html_loader_normalizes_fragment_and_query_from_section_links() -> None:
    pages = {
        ENTRY_URL: '<div id="bookToc"><a href="book/configuring_vlans.html?view=all#vlan">VLANs</a></div>',
        SECTION_URL: '<div id="pageContentDiv"><h2>Configuring VLANs</h2><p>Text</p></div>',
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    assert sections[0].section_url == SECTION_URL


def test_html_loader_splits_page_at_heading_anchors() -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <article>
            <h2 id="configuring-vlans">Configuring VLANs</h2>
            <p>Create and modify VLANs.</p>
            <h2 id="creating-ethernet-vlan">Creating or Modifying an Ethernet VLAN</h2>
            <p>Use VLAN configuration mode.</p>
            <pre><code>Device(config)# vlan 100</code></pre>
          </article>
        </div>
        """,
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    assert [section.section_title for section in sections] == [
        "Configuring VLANs",
        "Creating or Modifying an Ethernet VLAN",
    ]
    assert [section.section_url for section in sections] == [
        f"{SECTION_URL}#configuring-vlans",
        f"{SECTION_URL}#creating-ethernet-vlan",
    ]
    assert "Create and modify VLANs." in sections[0].text
    assert "Use VLAN configuration mode." in sections[1].text
    assert sections[1].code_blocks == ("Device(config)# vlan 100",)


def test_html_loader_attaches_auxiliary_example_heading_to_previous_section() -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <h2 id="creating-vlan">Creating or Modifying an Ethernet VLAN</h2>
          <p>Use VLAN configuration mode.</p>
          <h4 class="sectiontitle tasklabel">Example:</h4>
          <pre><code>Device(config)# vlan 100</code></pre>
          <h4 class="sectiontitle tasklabel">Before you begin</h4>
          <p>Confirm the VTP mode.</p>
        </div>
        """,
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    assert len(sections) == 1
    assert sections[0].section_title == "Creating or Modifying an Ethernet VLAN"
    assert "Use VLAN configuration mode." in sections[0].text
    assert "Confirm the VTP mode." in sections[0].text
    assert sections[0].code_blocks == ("Device(config)# vlan 100",)


def test_html_loader_attaches_tasklabel_h3_to_previous_section() -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <h2 id="creating-vlan">Creating or Modifying an Ethernet VLAN</h2>
          <h3 class="sectiontitle tasklabel">Example:</h3>
          <pre><code>Device(config)# vlan 100</code></pre>
        </div>
        """,
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    assert len(sections) == 1
    assert sections[0].section_title == "Creating or Modifying an Ethernet VLAN"


def test_html_loader_preserves_structural_units() -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <article id="article-vlan" class="topic reference">
            <h2 id="creating-vlan">Creating VLANs</h2>
            <section id="procedure-vlan" class="taskbody">
              <p>Create a VLAN.</p>
              <pre id="command-vlan" class="codeblock"><code>vlan 100</code></pre>
              <table id="command-table" class="step-table">
                <tr><th>Command</th><th>Purpose</th></tr>
                <tr><td>name USERS</td><td>Set VLAN name</td></tr>
              </table>
            </section>
          </article>
        </div>
        """,
    }

    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()

    units = sections[0].structural_units
    assert {unit.unit_type for unit in units} == {"article", "section", "pre", "table"}
    pre = next(unit for unit in units if unit.unit_type == "pre")
    table = next(unit for unit in units if unit.unit_type == "table")
    assert pre.element_id == "command-vlan"
    assert pre.class_name == "codeblock"
    assert pre.text == "vlan 100"
    assert table.element_id == "command-table"
    assert "name USERS" in table.text


def test_html_loader_excludes_layout_and_cookie_noise() -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <header>Header noise</header>
          <nav id="chapterToc"><a href="#ignored">Contents noise</a></nav>
          <div id="cookie-banner"><p>Cookie noise</p></div>
          <article id="article-vlan">
            <h2 id="creating-vlan">Creating VLANs</h2>
            <p>Keep this configuration explanation.</p>
            <pre><code>vlan 100</code></pre>
            <section id="feedback-panel" class="feedback"><p>Feedback noise</p></section>
          </article>
          <footer>Footer noise</footer>
        </div>
        """,
    }

    section = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()[0]

    assert "Keep this configuration explanation." in section.text
    assert section.code_blocks == ("vlan 100",)
    for noise in ("Header noise", "Contents noise", "Cookie noise", "Feedback noise", "Footer noise"):
        assert noise not in section.text
        assert all(noise not in unit.text for unit in section.structural_units)


    def test_rendered_html_fetcher_returns_browser_dom() -> None:
      fetcher = RenderedHtmlFetcher(
        render_page=lambda url: f"<html><body><main>{url}</main></body></html>"
      )

      rendered = fetcher.render(SECTION_URL)

      assert rendered.url == SECTION_URL
      assert rendered.browser == "chromium"
      assert rendered.selector == "document.documentElement"
      assert SECTION_URL in rendered.html


    def test_rendered_loader_stores_rendered_html_separately(tmp_path: Path) -> None:
      pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: '<div id="pageContentDiv"><h2 id="vlan">VLANs</h2><p>Rendered</p></div>',
      }
      metadata_path = tmp_path / "fetches.jsonl"
      loader = HtmlLoader.rendered(
        ENTRY_URL,
        "doc-1",
        rendered_storage_dir=tmp_path / "rendered_html",
        metadata_path=metadata_path,
        render_page=pages.__getitem__,
      )

      loader.load()

      rendered_files = list((tmp_path / "rendered_html" / "doc-1").glob("*.html"))
      assert len(rendered_files) == 2
      records = [json.loads(line) for line in metadata_path.read_text().splitlines()]
      assert all(record["retrieval_method"] == "rendered" for record in records)
      assert all(record["stored_path"].endswith(".html") for record in records)


    def test_html_loader_stores_html_by_content_hash(tmp_path: Path) -> None:
      html = "<div id=\"pageContentDiv\"><h2 id=\"vlan\">VLANs</h2><p>Text</p></div>"
      pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: html,
      }

      HtmlLoader(
        ENTRY_URL,
        "doc-1",
        fetch_html=pages.__getitem__,
        storage_dir=tmp_path,
      ).load()

      stored_files = list((tmp_path / "doc-1").glob("*.html"))
      assert len(stored_files) == 2
      assert stored_files[0].read_text(encoding="utf-8") in pages.values()
      assert all(len(path.stem) == 64 for path in stored_files)


    def test_html_loader_writes_fetch_metadata(tmp_path: Path) -> None:
      pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: '<div id="pageContentDiv"><h2 id="vlan">VLANs</h2><p>Text</p></div>',
      }
      metadata_path = tmp_path / "html_fetches.jsonl"

      HtmlLoader(
        ENTRY_URL,
        "doc-1",
        fetch_html=pages.__getitem__,
        metadata_path=metadata_path,
      ).load()

      records = [json.loads(line) for line in metadata_path.read_text().splitlines()]
      assert len(records) == 2
      assert all(record["status"] == "success" for record in records)
      assert all(record["attempts"] == 1 for record in records)
      assert all(len(record["sha256"]) == 64 for record in records)


    def test_html_loader_writes_failure_record_and_retries(tmp_path: Path) -> None:
      calls = 0

      def fetch(_: str) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("unavailable")

      metadata_path = tmp_path / "html_fetches.jsonl"
      loader = HtmlLoader(
        ENTRY_URL,
        "doc-1",
        fetch_html=fetch,
        metadata_path=metadata_path,
        max_retries=2,
      )

      try:
        loader.load()
      except RuntimeError as error:
        assert str(error) == "unavailable"
      else:
        raise AssertionError("expected RuntimeError")

      record = json.loads(metadata_path.read_text().splitlines()[0])
      assert calls == 3
      assert record["status"] == "failure"
      assert record["attempts"] == 3
      assert record["error_type"] == "RuntimeError"


    def test_normalized_html_sections_are_saved_as_jsonl(tmp_path: Path) -> None:
      section = HtmlLoader(
        ENTRY_URL,
        "doc-1",
        fetch_html={
          ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
          SECTION_URL: '<div id="pageContentDiv"><h2 id="vlan">VLANs</h2><p>Text</p></div>',
        }.__getitem__,
      ).load()[0]
      output = tmp_path / "processed" / "html_blocks.jsonl"

      assert write_html_blocks(output, [section]) == 1
      record = json.loads(output.read_text(encoding="utf-8"))
      assert record["document_id"] == "doc-1"
      assert record["section_url"] == f"{SECTION_URL}#vlan"
      assert record["structural_units"] == []


    def test_normalized_record_contains_rule_based_annotation() -> None:
      section = HtmlLoader(
        ENTRY_URL,
        "doc-1",
        fetch_html={
          ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
          SECTION_URL: """
          <div id="pageContentDiv"><h2 id="vlan">Creating VLANs</h2>
          <pre><code>Device(config)# vlan 100
    Device(config-vlan)# name USERS</code></pre>
          <table><tr><th>Command</th><th>Purpose</th></tr>
          <tr><td>show vlan brief</td><td>Verify</td></tr></table></div>
          """,
        }.__getitem__,
      ).load()[0]

      annotation = section_to_record(section)["rule_annotation"]

      assert annotation["extraction_method"] == "rule_based"
      assert annotation["content_type"] == "configuration_example"
      assert [command["role"] for command in annotation["commands"]] == [
        "enter_vlan",
        "set_name",
        "verification",
      ]
      assert annotation["needs_llm"] is False


def test_only_ambiguous_sections_are_written_to_llm_candidates(tmp_path: Path) -> None:
    pages = {
        ENTRY_URL: f'<div id="bookToc"><a href="{SECTION_URL}">VLANs</a></div>',
        SECTION_URL: """
        <div id="pageContentDiv">
          <h2 id="clear">Clear command</h2>
          <pre><code>Device(config)# vlan 100</code></pre>
          <h2 id="ambiguous">Ambiguous explanation</h2>
          <p>This section describes VLAN behavior and prerequisites.</p>
        </div>
        """,
    }
    sections = HtmlLoader(ENTRY_URL, "doc-1", fetch_html=pages.__getitem__).load()
    output = tmp_path / "html_llm_candidates.jsonl"

    assert write_llm_candidates(output, sections) == 1
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["section_title"] == "Ambiguous explanation"
    assert record["rule_annotation"]["needs_llm"] is True