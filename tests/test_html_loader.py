from app.ingestion.html_loader import HtmlLoader


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