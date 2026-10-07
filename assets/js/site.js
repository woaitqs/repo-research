// Repo Research — progressive enhancements. Every page renders and navigates
// without this script; it adds the theme toggle, the small-screen drawer, the
// article outline, reading aids and Mermaid rendering.
(function () {
  "use strict";

  var root = document.documentElement;
  var zh = (root.lang || "").slice(0, 2) === "zh";
  var strings = zh
    ? { copy: "复制", copied: "已复制", anchor: "链接到本节", expand: "放大查看", close: "关闭", figure: "图" }
    : { copy: "Copy", copied: "Copied", anchor: "Link to this section", expand: "Expand", close: "Close", figure: "Diagram" };

  function storeGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function storeSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }
  function headerHeight() {
    return parseFloat(getComputedStyle(root).getPropertyValue("--header-h")) || 72;
  }
  function each(selector, fn, scope) {
    Array.prototype.forEach.call((scope || document).querySelectorAll(selector), fn);
  }

  // ---- Theme ----------------------------------------------------------------
  function setTheme(theme, remember) {
    root.dataset.theme = theme;
    if (remember) {
      storeSet("rr-theme", theme);
      // The Archify diagrams served from this site read this key, so they
      // open in the theme the reader picked here.
      storeSet("archify-theme", theme);
    }
    document.dispatchEvent(new CustomEvent("rr:themechange"));
  }

  var themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    themeToggle.hidden = false;
    themeToggle.addEventListener("click", function () {
      setTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
    });
  }

  var darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  if (darkQuery.addEventListener) {
    darkQuery.addEventListener("change", function (event) {
      if (!storeGet("rr-theme")) setTheme(event.matches ? "dark" : "light", false);
    });
  }

  // ---- Drawer (small screens) -------------------------------------------
  var drawer = document.getElementById("nav-drawer");
  var drawerOpen = document.querySelector("[data-drawer-open]");
  if (drawer && drawerOpen && typeof drawer.showModal === "function") {
    drawerOpen.addEventListener("click", function () {
      drawer.showModal();
      drawerOpen.setAttribute("aria-expanded", "true");
    });
    drawer.addEventListener("close", function () {
      drawerOpen.setAttribute("aria-expanded", "false");
    });
    drawer.addEventListener("click", function (event) {
      var rect = drawer.getBoundingClientRect();
      var outside = event.clientX > rect.right || event.clientX < rect.left ||
        event.clientY > rect.bottom || event.clientY < rect.top;
      if (outside || event.target.closest("a, [data-drawer-close]")) drawer.close();
    });
  }

  // ---- Popovers (<details>) close on outside click / Escape ---------------
  function closePopovers(except) {
    each("details.language-picker[open], details.text-size[open]", function (d) {
      if (!except || !d.contains(except)) d.open = false;
    });
  }
  document.addEventListener("click", function (event) { closePopovers(event.target); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closePopovers(null);
  });

  // ---- Article --------------------------------------------------------------
  var article = document.querySelector("[data-article]");
  if (!article) return;

  // Mermaid: kramdown renders ```mermaid fences as highlighted code. Turn them
  // back into Mermaid sources so the same Markdown renders on GitHub and here.
  var mermaidBlocks = [];
  each("div.language-mermaid, pre > code.language-mermaid", function (el) {
    var code = el.tagName === "CODE" ? el : el.querySelector("code");
    if (!code) return;
    var target = el.tagName === "CODE" ? el.parentElement : el;
    var figure = document.createElement("div");
    figure.className = "mermaid-figure";
    var pre = document.createElement("pre");
    pre.className = "mermaid";
    pre.dataset.source = code.textContent;
    pre.textContent = code.textContent;
    var expand = document.createElement("button");
    expand.type = "button";
    expand.className = "figure-expand";
    expand.textContent = strings.expand;
    expand.hidden = true;
    expand.addEventListener("click", function () { openFigure(pre); });
    figure.appendChild(pre);
    figure.appendChild(expand);
    target.replaceWith(figure);
    mermaidBlocks.push(pre);
  }, article);

  var figureDialog = null;
  function openFigure(pre) {
    var svg = pre.querySelector("svg");
    if (!svg) return;
    if (!figureDialog) {
      figureDialog = document.createElement("dialog");
      figureDialog.className = "figure-dialog";
      figureDialog.setAttribute("aria-label", strings.figure);
      figureDialog.innerHTML = '<div class="figure-dialog-head"><span></span>' +
        '<button type="button" class="icon-button" data-figure-close></button></div>' +
        '<div class="figure-dialog-body"></div>';
      var close = figureDialog.querySelector("[data-figure-close]");
      close.setAttribute("aria-label", strings.close);
      close.textContent = "×";
      close.addEventListener("click", function () { figureDialog.close(); });
      figureDialog.addEventListener("click", function (event) {
        if (event.target === figureDialog) figureDialog.close();
      });
      document.body.appendChild(figureDialog);
    }
    var section = null;
    for (var node = pre.closest(".mermaid-figure"); node; node = node.previousElementSibling) {
      if (/^H[23]$/.test(node.tagName)) { section = node; break; }
    }
    figureDialog.querySelector(".figure-dialog-head span").textContent =
      section ? section.dataset.outlineLabel || section.textContent : strings.figure;
    var body = figureDialog.querySelector(".figure-dialog-body");
    var clone = svg.cloneNode(true);
    clone.style.width = clone.dataset.naturalWidth + "px";
    body.innerHTML = "";
    body.appendChild(clone);
    figureDialog.showModal();
  }

  // Fit wide diagrams to the column, but never below 60% of their natural
  // size (text stays readable); anything wider scrolls or opens in the dialog.
  function fitDiagrams() {
    mermaidBlocks.forEach(function (pre) {
      var svg = pre.querySelector("svg");
      if (!svg) return;
      if (!svg.dataset.naturalWidth) {
        var natural = parseFloat(svg.getAttribute("width")) ||
          (svg.viewBox && svg.viewBox.baseVal ? svg.viewBox.baseVal.width : 0);
        if (!natural) return;
        svg.dataset.naturalWidth = natural;
      }
      var width = parseFloat(svg.dataset.naturalWidth);
      var room = pre.clientWidth - 32;
      var scale = width <= room ? 1 : Math.max(room / width, 0.6);
      svg.style.width = Math.round(width * scale) + "px";
    });
  }

  if (mermaidBlocks.length) {
    var mermaidReady = import("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs")
      .then(function (module) { return module.default; });

    var mermaidTheme = function () {
      var cs = getComputedStyle(root);
      var v = function (name) { return cs.getPropertyValue(name).trim(); };
      return {
        darkMode: root.dataset.theme === "dark",
        fontFamily: "Manrope, system-ui, sans-serif",
        fontSize: "14px",
        background: v("--surface"),
        mainBkg: v("--bg"),
        primaryColor: v("--bg"),
        primaryTextColor: v("--ink"),
        primaryBorderColor: v("--muted"),
        secondaryColor: v("--surface-2"),
        tertiaryColor: v("--surface"),
        textColor: v("--ink"),
        lineColor: v("--muted"),
        nodeBorder: v("--muted"),
        clusterBkg: v("--surface-2"),
        clusterBorder: v("--line"),
        edgeLabelBackground: v("--surface"),
        actorBkg: v("--bg"),
        actorBorder: v("--muted"),
        actorTextColor: v("--ink"),
        actorLineColor: v("--line"),
        signalColor: v("--ink"),
        signalTextColor: v("--ink"),
        labelBoxBkgColor: v("--surface-2"),
        labelBoxBorderColor: v("--line"),
        labelTextColor: v("--ink"),
        loopTextColor: v("--ink"),
        noteBkgColor: v("--accent"),
        noteTextColor: v("--accent-ink"),
        noteBorderColor: v("--rule-accent"),
        activationBkgColor: v("--surface-2"),
        activationBorderColor: v("--muted"),
        sequenceNumberColor: v("--bg")
      };
    };

    var renderMermaid = function () {
      return mermaidReady.then(function (mermaid) {
        mermaidBlocks.forEach(function (pre) {
          pre.removeAttribute("data-processed");
          pre.textContent = pre.dataset.source;
        });
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: mermaidTheme(),
          flowchart: { useMaxWidth: false },
          sequence: { useMaxWidth: false, actorFontSize: 14, messageFontSize: 14, noteFontSize: 13 },
          class: { useMaxWidth: false },
          state: { useMaxWidth: false }
        });
        return mermaid.run({ nodes: mermaidBlocks });
      }).then(function () {
        fitDiagrams();
        mermaidBlocks.forEach(function (pre) {
          var expand = pre.parentNode.querySelector(".figure-expand");
          if (expand) expand.hidden = !pre.querySelector("svg");
        });
      }).catch(function (error) {
        mermaidBlocks.forEach(function (pre) { pre.classList.add("is-failed"); });
        if (window.console) console.warn("Mermaid rendering failed", error);
      });
    };
    renderMermaid();
    document.addEventListener("rr:themechange", renderMermaid);
    window.addEventListener("resize", fitDiagrams);
  }

  // Code blocks: language label + copy button.
  each("div.highlighter-rouge", function (block) {
    var match = block.className.match(/language-(\S+)/);
    var lang = match ? match[1] : "";
    if (lang === "plaintext") lang = "text";
    var bar = document.createElement("div");
    bar.className = "code-toolbar";
    var label = document.createElement("span");
    label.textContent = lang;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "copy-code";
    button.textContent = strings.copy;
    button.addEventListener("click", function () {
      var code = block.querySelector("code");
      if (!code || !navigator.clipboard) return;
      navigator.clipboard.writeText(code.textContent).then(function () {
        button.textContent = strings.copied;
        setTimeout(function () { button.textContent = strings.copy; }, 1600);
      });
    });
    bar.appendChild(label);
    bar.appendChild(button);
    block.insertBefore(bar, block.firstChild);
  }, article);

  // Reading time: CJK characters at ~400/min, other words at ~230/min.
  var readingTime = document.querySelector("[data-reading-time]");
  if (readingTime) {
    var text = article.textContent;
    var cjkPattern = /[㐀-鿿豈-﫿]/g;
    var cjk = (text.match(cjkPattern) || []).length;
    var words = (text.replace(cjkPattern, " ").match(/[A-Za-z0-9_]+/g) || []).length;
    var minutes = Math.max(1, Math.round(cjk / 400 + words / 230));
    readingTime.textContent = readingTime.getAttribute("data-reading-time").replace("{n}", minutes);
  }

  // Outline + heading anchors.
  var headings = Array.prototype.filter.call(article.querySelectorAll("h2[id], h3[id]"), function (h) {
    return !h.closest("blockquote, li");
  });
  var outlineLinks = [];

  headings.forEach(function (heading) {
    var label = heading.textContent.trim();
    // Keep the outline short: drop a trailing parenthetical, e.g. the English
    // title after a Chinese heading or "(assembler)" after a class name.
    var short = label.replace(/\s*[（(][^（）()]*[）)]$/, "").trim();
    if (short) label = short;
    heading.dataset.outlineLabel = label;

    var anchor = document.createElement("a");
    anchor.className = "heading-anchor";
    anchor.href = "#" + heading.id;
    anchor.textContent = "#";
    anchor.setAttribute("aria-label", strings.anchor);
    heading.insertBefore(anchor, heading.firstChild);
  });

  if (headings.length > 1) {
    each("[data-outline]", function (nav) {
      headings.forEach(function (heading) {
        var link = document.createElement("a");
        link.href = "#" + heading.id;
        link.textContent = heading.dataset.outlineLabel;
        link.dataset.target = heading.id;
        if (heading.tagName === "H3") link.className = "is-sub";
        nav.appendChild(link);
        outlineLinks.push(link);
      });
    });
    each("[data-outline-wrap]", function (wrap) { wrap.hidden = false; });
    each(".mobile-outline nav a", function (link) {
      link.addEventListener("click", function () { link.closest("details").open = false; });
    });
  }

  var outlineRail = document.querySelector(".outline-rail");
  var activeHeading = null;

  function updateOutline() {
    if (!headings.length) return;
    var offset = headerHeight() + 96;
    var active = null;
    for (var i = 0; i < headings.length; i++) {
      if (headings[i].getBoundingClientRect().top - offset <= 0) active = headings[i];
      else break;
    }
    if (active === activeHeading) return;
    activeHeading = active;
    var id = active ? active.id : null;
    outlineLinks.forEach(function (link) {
      if (link.dataset.target === id) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    // Keep the active entry visible inside the rail without moving the page.
    if (outlineRail && id && outlineRail.offsetParent !== null) {
      var current = outlineRail.querySelector('a[aria-current="location"]');
      if (current) {
        var railBox = outlineRail.getBoundingClientRect();
        var linkBox = current.getBoundingClientRect();
        if (linkBox.top < railBox.top + 48 || linkBox.bottom > railBox.bottom - 48) {
          outlineRail.scrollTop += linkBox.top - railBox.top - railBox.height / 3;
        }
      }
    }
  }

  // Reading progress.
  var progress = document.querySelector("[data-progress]");
  function updateProgress() {
    if (!progress) return;
    var box = article.getBoundingClientRect();
    var top = headerHeight();
    var total = box.height - (window.innerHeight - top);
    var ratio = total > 0 ? (top - box.top) / total : 1;
    progress.style.transform = "scaleX(" + Math.min(1, Math.max(0, ratio)) + ")";
  }

  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      updateOutline();
      updateProgress();
    });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  onScroll();

  // Text size and focus mode.
  var controls = document.querySelector("[data-reader-controls]");
  if (controls) {
    controls.hidden = false;
    var steps = [0.85, 0.92, 1, 1.08, 1.16, 1.24];
    var output = controls.querySelector("[data-text-size-value]");
    var scale = parseFloat(getComputedStyle(root).getPropertyValue("--reading-scale")) || 1;
    var showScale = function () { if (output) output.textContent = Math.round(scale * 100) + "%"; };
    showScale();

    each("[data-text-size]", function (button) {
      button.addEventListener("click", function () {
        var step = parseInt(button.getAttribute("data-text-size"), 10);
        if (step === 0) {
          scale = 1;
        } else {
          var index = 0;
          steps.forEach(function (s, i) {
            if (Math.abs(s - scale) < Math.abs(steps[index] - scale)) index = i;
          });
          scale = steps[Math.min(steps.length - 1, Math.max(0, index + step))];
        }
        root.style.setProperty("--reading-scale", scale);
        storeSet("rr-text-scale", String(scale));
        showScale();
        onScroll();
      });
    }, controls);

    var focusToggle = controls.querySelector("[data-focus-toggle]");
    if (focusToggle) {
      focusToggle.addEventListener("click", function () {
        var on = root.classList.toggle("focus-reading");
        focusToggle.setAttribute("aria-pressed", on ? "true" : "false");
        window.dispatchEvent(new Event("resize"));   // re-fit diagrams, update outline
      });
    }
  }
})();
