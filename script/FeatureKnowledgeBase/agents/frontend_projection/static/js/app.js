(function () {
  "use strict";

  var state = { features: [], selected: null, query: "", type: "", status: "" };
  var $ = function (id) { return document.getElementById(id); };

  function text(value) { return value == null ? "" : String(value); }
  function escapeText(value) { return text(value); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function element(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = value;
    return node;
  }
  function badge(value, className) { return element("span", "badge " + (className || ""), value); }
  function statusClass(status) { return "status-" + text(status).toLowerCase(); }

  function filtered() {
    var q = state.query.toLowerCase();
    return state.features.filter(function (feature) {
      if (state.type && feature.type !== state.type) return false;
      if (state.status && feature.semantic_status !== state.status) return false;
      if (!q) return true;
      return [feature.name, feature.normalized_identity, feature.key, feature.description]
        .concat(list(feature.syntax)).join(" ").toLowerCase().indexOf(q) >= 0;
    });
  }

  function renderSummary(summary) {
    var target = $("summary");
    clear(target);
    var values = [
      [summary.projected_feature_count, "Features"],
      [summary.feature_type_counts.FUNCTION || 0, "Functions"],
      [summary.feature_type_counts.OPERATOR || 0, "Operators"],
      [summary.feature_type_counts.DATATYPE || 0, "Datatypes"],
      [summary.with_official_examples, "With official examples"]
    ];
    values.forEach(function (item) {
      var card = element("div", "summary-card");
      card.appendChild(element("strong", "", item[0]));
      card.appendChild(element("span", "", item[1]));
      target.appendChild(card);
    });
    $("header-count").textContent = summary.projected_feature_count + " publishable features";
  }

  function renderFilters() {
    var types = Array.from(new Set(state.features.map(function (row) { return row.type; }))).sort();
    var statuses = Array.from(new Set(state.features.map(function (row) { return row.semantic_status; }))).sort();
    var typeSelect = $("type-filter");
    var statusSelect = $("status-filter");
    types.forEach(function (type) { typeSelect.appendChild(element("option", "", type)); });
    statuses.forEach(function (status) { statusSelect.appendChild(element("option", "", status)); });
    $("search").addEventListener("input", function (event) { state.query = event.target.value.trim(); renderList(); });
    typeSelect.addEventListener("change", function (event) { state.type = event.target.value; renderList(); });
    statusSelect.addEventListener("change", function (event) { state.status = event.target.value; renderList(); });
  }

  function renderList() {
    var target = $("feature-list");
    var rows = filtered();
    clear(target);
    $("result-count").textContent = rows.length + " feature" + (rows.length === 1 ? "" : "s");
    $("empty").hidden = rows.length !== 0;
    rows.forEach(function (feature) {
      var card = element("article", "feature-card" + (state.selected === feature.id ? " selected" : ""));
      card.tabIndex = 0;
      card.addEventListener("click", function () { select(feature); });
      card.addEventListener("keydown", function (event) { if (event.key === "Enter") select(feature); });
      var head = element("div", "feature-card-head");
      head.appendChild(element("div", "feature-name", feature.name));
      head.appendChild(element("div", "feature-key", feature.key));
      card.appendChild(head);
      var badges = element("div", "badges");
      badges.appendChild(badge(feature.type));
      badges.appendChild(badge(feature.sense));
      badges.appendChild(badge(feature.semantic_status, statusClass(feature.semantic_status)));
      card.appendChild(badges);
      card.appendChild(feature.description ? element("div", "description-preview", feature.description) : element("div", "description-preview missing", "No official description extracted."));
      target.appendChild(card);
    });
  }

  function section(title, content) {
    var wrapper = element("section", "detail-section");
    wrapper.appendChild(element("h3", "", title));
    wrapper.appendChild(content);
    return wrapper;
  }

  function renderDetail(feature) {
    var target = $("detail");
    clear(target);
    target.className = "detail";
    var title = element("h2", "", feature.name);
    target.appendChild(title);
    target.appendChild(element("div", "detail-key", feature.key));
    var badges = element("div", "badges");
    badges.appendChild(badge(feature.type));
    badges.appendChild(badge(feature.sense));
    badges.appendChild(badge(feature.semantic_status, statusClass(feature.semantic_status)));
    if (feature.semantic_execution_state) badges.appendChild(badge("Runtime: " + feature.semantic_execution_state));
    if (feature.field_validation_status) badges.appendChild(badge("Fields: " + feature.field_validation_status));
    target.appendChild(badges);

    var descriptions = element("div", "description-paragraphs");
    var descriptionParagraphs = list(feature.descriptions).length ? list(feature.descriptions) : list(feature.description);
    if (descriptionParagraphs.length) descriptionParagraphs.forEach(function (value) { descriptions.appendChild(element("p", "", value)); });
    else descriptions.appendChild(element("p", "missing", "No official description extracted."));
    target.appendChild(section("Description", descriptions));
    var syntax = element("div", "code-list");
    if (feature.syntax.length) feature.syntax.forEach(function (value) { syntax.appendChild(element("pre", "", value)); });
    else syntax.appendChild(element("p", "missing", "Official syntax not extracted."));
    target.appendChild(section("Formal syntax", syntax));
    var variants = element("div", "code-list");
    list(feature.identity_variants).forEach(function (value) { variants.appendChild(element("pre", "", value)); });
    target.appendChild(section("Identity variants (not formal syntax)", variants));
    target.appendChild(section("Aliases", element("p", "", list(feature.aliases).join(", ") || "Not available")));
    [["Parameters", feature.parameters], ["Return type", feature.return_type]].forEach(function (pair) {
      var content = element("div", "code-list");
      list(pair[1]).forEach(function (claim) {
        content.appendChild(element("p", "", (claim.name || claim.value || "") + (claim.description ? ": " + claim.description : "")));
        var proof = document.createElement("details");
        proof.appendChild(element("summary", "", "Field provenance"));
        proof.appendChild(element("pre", "", JSON.stringify({ evidence: claim.evidence, additional_provenance: claim.additional_provenance, evidence_refs: claim.evidence_refs }, null, 2)));
        content.appendChild(proof);
      });
      if (!list(pair[1]).length) content.appendChild(element("p", "missing", "Not available"));
      target.appendChild(section(pair[0], content));
    });

    var examples = element("div", "");
    if (feature.official_examples.length) {
      feature.official_examples.forEach(function (item) {
        var block = element("div", "example");
        block.appendChild(element("pre", "", item.sql || item.text || ""));
        if (item.source_url) {
          var link = element("a", "", "Official source");
          link.href = item.source_url; link.target = "_blank"; link.rel = "noopener";
          block.appendChild(link);
        }
        if (item.evidence) {
          var proof = document.createElement("details");
          proof.appendChild(element("summary", "", "Example provenance"));
          proof.appendChild(element("pre", "", JSON.stringify(item.evidence, null, 2)));
          block.appendChild(proof);
        }
        examples.appendChild(block);
      });
    } else examples.appendChild(element("p", "missing", "No official example extracted."));
    target.appendChild(section("Official examples", examples));

    var notes = element("div", "code-list");
    if (feature.notes.length) feature.notes.forEach(function (value) { notes.appendChild(element("p", "", value)); });
    else notes.appendChild(element("p", "missing", "No notes extracted."));
    target.appendChild(section("Notes", notes));

    var sources = element("div", "source-list");
    feature.sources.forEach(function (source) {
      var row = element("div", "source");
      var label = element("span", "", source.label);
      var link = element("a", "", "Open official source");
      link.href = source.url; link.target = "_blank"; link.rel = "noopener";
      row.appendChild(label); row.appendChild(link); sources.appendChild(row);
    });
    target.appendChild(section("Official sources", sources));

    var details = document.createElement("details");
    details.appendChild(element("summary", "", "Canonical / provenance"));
    var metadata = element("dl", "metadata");
    [["Canonical ID", feature.id], ["Route ID", feature.route_id], ["DBMS", feature.dbms], ["Semantic status", feature.semantic_status], ["Source count", feature.provenance.source_count]].forEach(function (item) {
      metadata.appendChild(element("dt", "", item[0])); metadata.appendChild(element("dd", "", item[1]));
    });
    details.appendChild(metadata); target.appendChild(details);
    var fieldProof = document.createElement("details");
    fieldProof.appendChild(element("summary", "", "Field provenance"));
    fieldProof.appendChild(element("pre", "", JSON.stringify(feature.field_provenance || {}, null, 2)));
    target.appendChild(fieldProof);
  }

  function select(feature) {
    state.selected = feature.id;
    window.location.hash = "feature/" + feature.route_id;
    renderList(); renderDetail(feature);
  }

  function routeId(value) {
    return text(value).replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "feature";
  }

  function sourceUrls(row) {
    var urls = list(row.source_anchors);
    if (!urls.length) {
      urls = list(row.evidence).map(function (item) { return item && (item.url || item.source_url); });
    }
    return Array.from(new Set(urls.filter(Boolean)));
  }

  // The v2 projection is embedded in js/data.js by the Generic pipeline.  The
  // legacy fallback below still supports the older features.json contract.
  function normalizeV2Feature(row) {
    var identity = row.canonical_identity || {};
    var id = text(row.feature_id || row.canonical_id);
    var urls = sourceUrls(row);
    var syntax = list(row.syntax);
    var examples = list(row.official_example_claims || row.examples).map(function (value) {
      if (typeof value === "object") {
        var evidence = value.evidence || {};
        return { text: value.value || value.sql || value.text || "", source_url: evidence.source_anchor || evidence.url || value.source_url || "", evidence: { primary: evidence, additional_provenance: value.additional_provenance, evidence_refs: value.evidence_refs } };
      }
      return { text: text(value), source_url: "" };
    });
    var notes = list(row.notes).map(function (value) {
      return typeof value === "object" ? (value.text || value.description || "") : text(value);
    }).filter(Boolean);
    return {
      id: id,
      key: text(row.canonical_identity_key || identity.key),
      route_id: routeId(id),
      dbms: text(row.dbms),
      name: text(row.name || identity.canonical_name),
      normalized_identity: text(identity.canonical_name || row.name),
      type: text(row.feature_type).toUpperCase(),
      sense: text(row.discriminator || identity.discriminator || "DEFAULT"),
      canonical_status: text(row.promotion_eligibility || "promotion_ready"),
      semantic_status: text(row.status || row.semantic_status || row.quality_status || "UNKNOWN").toUpperCase(),
      description: text(row.description || (list(row.descriptions)[0] || "")),
      descriptions: list(row.descriptions).length ? list(row.descriptions) : list(row.description),
      identity_variants: list(row.identity_variants),
      aliases: list(row.aliases),
      parameters:list(row.parameters),
      return_type:list(row.return_type),
      semantic_execution_state:row.semantic_execution_state,
      field_validation_status:row.field_validation_status,
      field_provenance:row.field_provenance || {},
      syntax: syntax,
      official_examples: examples,
      notes: notes,
      sources: urls.map(function (url) { return { url: url, label: "Official source" }; }),
      provenance: { source_count: urls.length }
    };
  }

  function v2Summary(features) {
    var counts = {};
    features.forEach(function (feature) { counts[feature.type] = (counts[feature.type] || 0) + 1; });
    return {
      projected_feature_count: features.length,
      feature_type_counts: counts,
      with_official_examples: features.filter(function (feature) { return feature.official_examples.length; }).length
    };
  }

  function renderLoadedFeatures(features, summary) {
    state.features = features;
    renderSummary(summary);
    renderFilters();
    renderList();
    var route = window.location.hash.replace(/^#feature\//, "");
    var found = state.features.find(function (row) { return row.route_id === route; });
    if (found) select(found);
  }

  function load() {
    if (Array.isArray(window.FEATURE_DATA)) {
      renderLoadedFeatures(window.FEATURE_DATA.map(normalizeV2Feature), v2Summary(window.FEATURE_DATA.map(normalizeV2Feature)));
      return;
    }
    Promise.all([fetch("features.json").then(function (r) { return r.json(); }), fetch("summary.json").then(function (r) { return r.json(); })])
      .then(function (payload) { renderLoadedFeatures(payload[0].features || [], payload[1]); })
      .catch(function (error) { $("detail").textContent = "Unable to load frontend projection: " + error; });
  }
  load();
}());
