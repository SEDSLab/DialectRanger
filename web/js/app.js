/* DialectRanger Feature Knowledge Base — App Logic */
(function() {
  const FEATURES = window.FEATURE_DATA || [];
  const REVIEWS = window.REVIEW_DATA || [];
  const STATS = window.STATS || { total: 0, dbms: {} };
  const DBMS_LIST = window.DBMS_LIST || [];
  const PAGE_SIZE = 80;

  // DBMS color index (cycle through 10 colors)
  const dbmsColorIndex = {};
  DBMS_LIST.forEach((d, i) => { dbmsColorIndex[d] = i % 10; });

  let showReview = false;

  let activeDbms = "";
  let activeType = "";
  let searchQuery = "";
  let expandedId = null;
  let visibleCount = PAGE_SIZE;
  let searchTimer = null;

  // ── State helpers ──────────────────────────
  function filteredFeatures() {
    const data = showReview ? REVIEWS : FEATURES;
    return data.filter(f => {
      if (activeDbms && f.dbms !== activeDbms) return false;
      if (!showReview && activeType && f.feature_type !== activeType) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const haystack = [
          f.name, f.signature, f.description,
          ...f.descriptions, ...f.syntax, ...f.examples,
          f.feature_id, f.status || "",
          ...(f.review_reason || []),
        ].join(" ").toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }

  function currentStats() {
    const filtered = filteredFeatures();
    const dbs = new Set(filtered.map(f => f.dbms));
    return { total: filtered.length, dbms: dbs.size };
  }

  // ── Render Header ─────────────────────────
  function renderHeader() {
    document.getElementById("header-total").textContent = STATS.total.toLocaleString() + " Features";
    document.getElementById("header-dbms").textContent = DBMS_LIST.length + " DBMS";
  }

  // ── Render Sidebar ────────────────────────
  function renderSidebar() {
    const nav = document.getElementById("sidebar-nav");
    nav.innerHTML = "";

    DBMS_LIST.forEach(dbms => {
      const s = STATS.dbms[dbms] || {};
      const total = (s.function || 0) + (s.operator || 0) + (s.datatype || 0);
      if (total === 0) return;

      const group = document.createElement("div");
      group.className = "dbms-group";

      const toggle = document.createElement("button");
      const childrenId = "dbms-features-" + dbms.replace(/[^a-z0-9-]/gi, "-").toLowerCase();
      toggle.className = "dbms-toggle" + (activeDbms === dbms ? " open" : "");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", activeDbms === dbms ? "true" : "false");
      toggle.setAttribute("aria-controls", childrenId);
      toggle.innerHTML = '<span class="arrow">&#9654;</span>' + dbms + '<span class="count">' + total + '</span>';
      toggle.onclick = function(e) {
        e.stopPropagation();
        if (activeDbms === dbms) {
          setFilter("", activeType);
        } else {
          setFilter(dbms, "");
        }
      };

      const children = document.createElement("div");
      children.id = childrenId;
      children.className = "dbms-children" + (activeDbms === dbms ? " open" : "");

      [
        ["function", "Functions", s.function || 0],
        ["operator", "Operators", s.operator || 0],
        ["datatype", "Data Types", s.datatype || 0],
        ["review", "Review", countReviews(dbms)],
      ].forEach(([ft, label, count]) => {
        if (count === 0) return;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ft-item" + (activeDbms === dbms && activeType === ft ? " active" : "");
        btn.innerHTML = label + ' <span class="ft-count">' + count + '</span>';
        btn.onclick = function(e) {
          e.stopPropagation();
          setFilter(dbms, activeType === ft && activeDbms === dbms ? "" : ft);
        };
        children.appendChild(btn);
      });

      group.appendChild(toggle);
      group.appendChild(children);
      nav.appendChild(group);
    });
  }

  // ── Render Stats Bar ──────────────────────
  function renderStats() {
    const all = showReview ? REVIEWS : FEATURES;
    const totalItems = showReview ? REVIEWS.length : STATS.total;
    const syn = all.filter(f => f.syntax.length > 0).length;
    const ex = all.filter(f => f.examples.length > 0).length;
    const desc = all.filter(f => f.description).length;
    const rv = REVIEWS.length;
    document.getElementById("stats-bar").innerHTML = [
      { label: showReview ? "Review Items" : "Features", val: showReview ? rv.toLocaleString() : STATS.total.toLocaleString() },
      { label: "DBMS", val: DBMS_LIST.length },
      { label: "With Syntax", val: Math.round(syn / (totalItems || 1) * 100) + "%" },
      { label: "With Description", val: Math.round(desc / (totalItems || 1) * 100) + "%" },
      { label: "With Examples", val: Math.round(ex / (totalItems || 1) * 100) + "%" },
    ].map(s => '<div class="stat-item"><span class="stat-val">' + s.val + '</span>' + s.label + '</div>').join("");
  }

  // ── Render Filters ────────────────────────
  function renderFilters() {
    const dbmsSel = document.getElementById("filter-dbms");
    const typeSel = document.getElementById("filter-type");

    dbmsSel.innerHTML = '<option value="">All DBMS</option>';
    DBMS_LIST.forEach(d => {
      dbmsSel.innerHTML += '<option value="' + d + '"' + (activeDbms === d ? " selected" : "") + '>' + d + '</option>';
    });
    dbmsSel.onchange = function() { setFilter(dbmsSel.value, activeType); };

    typeSel.innerHTML = '<option value="">All Feature Types</option>';
    ["function", "operator", "datatype", "review"].forEach(ft => {
      const labels = { function: "Functions", operator: "Operators", datatype: "Data Types", review: "Review" };
      typeSel.innerHTML += '<option value="' + ft + '"' + (activeType === ft ? " selected" : "") + '>' + labels[ft] + '</option>';
    });
    typeSel.onchange = function() { setFilter(activeDbms, typeSel.value); };

    // Search
    const searchEl = document.getElementById("search-input");
    searchEl.value = searchQuery;
    searchEl.oninput = function() {
      searchQuery = searchEl.value.trim();
      visibleCount = PAGE_SIZE;
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(renderFeatureList, 120);
    };
  }

  // ── Set Filter ────────────────────────────
  function setFilter(dbms, type) {
    activeDbms = dbms || "";
    activeType = type || "";
    showReview = (type === "review");
    visibleCount = PAGE_SIZE;
    expandedId = null;
    document.getElementById("search-input").value = searchQuery;
    renderAll();
  }

  function countReviews(dbms) {
    return REVIEWS.filter(r => r.dbms === dbms).length;
  }

  // ── Render Feature List ──────────────────
  function renderFeatureList() {
    const list = document.getElementById("feature-list");
    const empty = document.getElementById("empty-state");
    const header = document.getElementById("results-header");
    const loadMore = document.getElementById("load-more");
    const filtered = filteredFeatures();
    const visible = filtered.slice(0, visibleCount);
    const st = currentStats();

    // Results header
    if (searchQuery) {
      header.innerHTML = 'Search results for &ldquo;<b>' + escapeHtml(searchQuery) + '</b>&rdquo;<span class="count">' + st.total + ' features</span>';
    } else if (showReview && activeDbms) {
      header.innerHTML = '<b>' + activeDbms + '</b> / Review<span class="count">' + st.total + ' items</span>';
    } else if (showReview) {
      header.innerHTML = 'All Review Items<span class="count">' + st.total + ' items</span>';
    } else if (activeDbms && activeType) {
      header.innerHTML = '<b>' + activeDbms + '</b> / ' + capitalize(activeType) + '<span class="count">' + st.total + ' features</span>';
    } else if (activeDbms) {
      header.innerHTML = '<b>' + activeDbms + '</b><span class="count">' + st.total + ' features across all types</span>';
    } else {
      header.innerHTML = 'All Features<span class="count">' + st.total + ' features across ' + st.dbms + ' DBMS</span>';
    }

    if (filtered.length === 0) {
      list.innerHTML = "";
      empty.hidden = false;
      loadMore.hidden = true;
      return;
    }
    empty.hidden = true;

    list.innerHTML = visible.map(f => {
      const fid = f.feature_id;
      const isExpanded = expandedId === fid;
      const isReview = f.is_review;
      const ftLabel = capitalize(f.feature_type);
      const hasSource = f.source_anchors && f.source_anchors.length > 0;
      const sourceUrl = hasSource ? f.source_anchors[0] : "";
      const dbmClass = "dbms-" + (dbmsColorIndex[f.dbms] || 0);

      let html = '<article class="feature-card ' + dbmClass + (isExpanded ? ' expanded' : '') + (isReview ? ' review-card' : '') + '" data-fid="' + escapeHtml(fid) + '">';
      html += '<button type="button" class="card-header" data-feature-id="' + escapeHtml(fid) + '" aria-expanded="' + isExpanded + '"><span class="card-name">' + escapeHtml(f.name) + '</span><span class="card-expand" aria-hidden="true">&#9660;</span></button>';
      html += '<div class="card-badges"><span class="badge badge-dbms">' + escapeHtml(f.dbms) + '</span><span class="badge badge-type">' + ftLabel + '</span>';
      if (isReview) {
        html += '<span class="badge badge-review">Review</span>';
      }
      html += '</div>';

      // Review reason
      if (isReview && f.review_reason) {
        const reasons = Array.isArray(f.review_reason) ? f.review_reason : [f.review_reason];
        const reasonText = reasons.filter(Boolean).join(", ");
        if (reasonText) {
          html += '<div class="card-review-reason">' + escapeHtml(reasonText) + '</div>';
        }
      }

      if (f.description) {
        html += '<div class="card-desc">' + escapeHtml(f.description) + '</div>';
      }
      if (f.signature) {
        html += '<div class="card-syntax">' + escapeHtml(f.signature) + '</div>';
      }

      // Sections display
      const sections = Array.from(new Set(f.source_sections_display || []));
      const secText = sections.length > 0 ? sections.slice(0, 3).join(" · ") : "";

      html += '<div class="card-footer">';
      html += '<span class="card-sources">' + escapeHtml(secText) + '</span>';
      html += '<span class="card-links">';
      if (sourceUrl) html += '<a href="' + escapeHtml(sourceUrl) + '" target="_blank" rel="noopener noreferrer">Official Docs &#8599;</a>';
      html += '</span></div>';

      // Expanded detail
      html += '<div class="detail-section">';

      // Description claims
      if (f.descriptions.length > 1) {
        html += '<div class="detail-title">Description</div>';
        f.descriptions.forEach(d => {
          html += '<div class="detail-text">' + escapeHtml(d) + '</div>';
        });
      }

      // Syntax
      if (f.syntax.length > 0) {
        html += '<div class="detail-title">Syntax</div>';
        f.syntax.forEach(s => {
          html += '<div class="detail-code">' + escapeHtml(s) + '</div>';
        });
      }

      // Examples
      if (f.examples.length > 0) {
        html += '<div class="detail-title">Examples</div>';
        f.examples.forEach(ex => {
          html += '<div class="detail-code">' + escapeHtml(ex) + '</div>';
        });
      }

      // Metadata
      html += '<div class="detail-title">Metadata</div>';
      html += '<dl class="detail-metadata">';
      html += '<dt>Database</dt><dd>' + escapeHtml(f.dbms) + '</dd>';
      html += '<dt>Feature Type</dt><dd>' + ftLabel + '</dd>';
      html += '<dt>Feature ID</dt><dd style="font-family:monospace;font-size:11px">' + escapeHtml(fid) + '</dd>';
      if (f.status) html += '<dt>Status</dt><dd>' + escapeHtml(f.status) + '</dd>';
      html += '</dl>';

      // Source Sections
      if (sections.length > 0) {
        html += '<div class="detail-title">Source Sections</div>';
        html += '<div class="detail-sections">';
        sections.forEach(ss => {
          html += '<span>' + escapeHtml(ss) + '</span>';
        });
        html += '</div>';
      }

      html += '</div>'; // detail-section
      html += '</article>'; // card
      return html;
    }).join("");

    list.querySelectorAll(".card-header").forEach(function(button) {
      button.addEventListener("click", function() {
        window._toggleCard(button.dataset.featureId);
      });
    });

    const remaining = filtered.length - visible.length;
    loadMore.hidden = remaining <= 0;
    loadMore.textContent = remaining > 0
      ? "Load " + Math.min(PAGE_SIZE, remaining) + " more features"
      : "";
  }

  // ── Card Expand/Collapse ─────────────────
  window._toggleCard = function(fid) {
    expandedId = expandedId === fid ? null : fid;
    renderFeatureList();
  };

  // ── Render All ────────────────────────────
  function renderAll() {
    renderSidebar();
    renderStats();
    renderFilters();
    renderFeatureList();
  }

  // ── Helpers ───────────────────────────────
  function capitalize(s) {
    if (!s) return "";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function escapeHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ── Init ──────────────────────────────────
  renderHeader();
  renderAll();

  document.getElementById("load-more").addEventListener("click", function() {
    visibleCount += PAGE_SIZE;
    renderFeatureList();
  });

  // Search submit on Enter
  document.getElementById("search-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      searchQuery = this.value.trim();
      renderFeatureList();
    }
  });

  // Tab switching
  document.querySelectorAll(".tab-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      document.querySelectorAll(".tab-btn").forEach(function(b) {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      document.querySelectorAll(".tab-content").forEach(function(c) {
        c.classList.remove("active");
        c.hidden = true;
      });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      var target = document.getElementById("tab-" + btn.dataset.tab);
      if (target) {
        target.classList.add("active");
        target.hidden = false;
      }
    });

    btn.addEventListener("keydown", function(event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const tabs = Array.from(document.querySelectorAll(".tab-btn"));
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(tabs.indexOf(btn) + direction + tabs.length) % tabs.length];
      next.focus();
      next.click();
    });
  });
})();
