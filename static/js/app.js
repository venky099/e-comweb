/* ==========================================================================
   Storefront behaviour.
   Deliberately thin: prices, totals, stock and discounts are all computed on
   the server. Nothing here decides what anything costs.
   ========================================================================== */
(function () {
  "use strict";

  // ---- CSRF for htmx requests -------------------------------------------
  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return match ? decodeURIComponent(match[2]) : null;
  }

  document.body.addEventListener("htmx:configRequest", function (event) {
    const token = getCookie("csrftoken");
    if (token) {
      event.detail.headers["X-CSRFToken"] = token;
    }
  });

  // ---- Flash-sale countdown ---------------------------------------------
  function pad(value) {
    return String(value).padStart(2, "0");
  }

  document.querySelectorAll("[data-countdown]").forEach(function (element) {
    let remaining = parseInt(element.dataset.countdown, 10);
    const display = element.querySelector("[data-countdown-display]");
    if (!display || isNaN(remaining)) return;

    function tick() {
      if (remaining <= 0) {
        display.textContent = "Ended";
        return;
      }
      const hours = Math.floor(remaining / 3600);
      const minutes = Math.floor((remaining % 3600) / 60);
      const seconds = remaining % 60;
      display.textContent = pad(hours) + ":" + pad(minutes) + ":" + pad(seconds);
      remaining -= 1;
      window.setTimeout(tick, 1000);
    }
    tick();
  });

  // ---- Copy a coupon code ------------------------------------------------
  document.querySelectorAll("[data-copy]").forEach(function (element) {
    function copy() {
      const code = element.dataset.copy;
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(code).then(function () {
        const original = element.innerHTML;
        element.innerHTML = code + ' <i class="bi bi-check-lg"></i>';
        window.setTimeout(function () {
          element.innerHTML = original;
        }, 1600);
      });
    }
    element.addEventListener("click", copy);
    element.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        copy();
      }
    });
  });

  // ---- Product gallery ---------------------------------------------------
  const galleryMain = document.querySelector("[data-gallery-main]");
  if (galleryMain) {
    document.querySelectorAll("[data-gallery-thumb]").forEach(function (thumb) {
      thumb.addEventListener("click", function () {
        galleryMain.src = thumb.dataset.galleryThumb;
        document
          .querySelectorAll("[data-gallery-thumb]")
          .forEach(function (other) {
            other.classList.toggle("is-active", other === thumb);
          });
      });
    });
  }

  // ---- Variant selector --------------------------------------------------
  // Reads the server-rendered variant matrix; asks the server for live stock
  // before enabling "add to cart".
  const variantRoot = document.querySelector("[data-variant-root]");
  if (variantRoot) {
    const matrix = JSON.parse(
      document.getElementById("variant-matrix").textContent || "[]"
    );
    const state = {
      size: variantRoot.dataset.selectedSize || "",
      color: variantRoot.dataset.selectedColor || "",
    };

    const priceNow = variantRoot.querySelector("[data-price-now]");
    const priceWas = variantRoot.querySelector("[data-price-was]");
    const priceOff = variantRoot.querySelector("[data-price-off]");
    const stockNote = variantRoot.querySelector("[data-stock-note]");
    const variantInput = variantRoot.querySelector("[data-variant-input]");
    const addButton = variantRoot.querySelector("[data-add-button]");
    const qtySelect = variantRoot.querySelector("[data-qty-select]");
    const currency = variantRoot.dataset.currency || "";

    function findVariant() {
      return matrix.find(function (variant) {
        const sizeOk = !state.size || variant.size === state.size;
        const colorOk = !state.color || variant.color === state.color;
        return sizeOk && colorOk;
      });
    }

    function refreshQuantityOptions(available) {
      if (!qtySelect) return;
      const cap = Math.min(available, parseInt(qtySelect.dataset.max, 10) || 10);
      qtySelect.innerHTML = "";
      for (let i = 1; i <= cap; i += 1) {
        const option = document.createElement("option");
        option.value = String(i);
        option.textContent = String(i);
        qtySelect.appendChild(option);
      }
      qtySelect.disabled = cap <= 0;
    }

    function apply(variant) {
      if (!variant) {
        if (stockNote) {
          stockNote.className = "text-danger small mb-3";
          stockNote.textContent = "That combination is not available.";
        }
        if (addButton) addButton.disabled = true;
        return;
      }

      if (variantInput) variantInput.value = variant.id;
      if (priceNow) priceNow.textContent = currency + Number(variant.price).toFixed(2);

      if (priceWas) {
        if (variant.compare_at_price && Number(variant.compare_at_price) > Number(variant.price)) {
          priceWas.textContent = currency + Number(variant.compare_at_price).toFixed(2);
          priceWas.hidden = false;
        } else {
          priceWas.hidden = true;
        }
      }
      if (priceOff) {
        if (variant.discount_percent > 0) {
          priceOff.textContent = "-" + variant.discount_percent + "%";
          priceOff.hidden = false;
        } else {
          priceOff.hidden = true;
        }
      }

      const available = Number(variant.available);
      if (stockNote) {
        if (available <= 0) {
          stockNote.className = "text-danger small mb-3";
          stockNote.textContent = "Out of stock";
        } else if (available <= 5) {
          stockNote.className = "text-warning small mb-3";
          stockNote.textContent = "Hurry - only " + available + " left";
        } else {
          stockNote.className = "text-success small mb-3";
          stockNote.textContent = "In stock";
        }
      }
      if (addButton) addButton.disabled = available <= 0;
      refreshQuantityOptions(available);
    }

    variantRoot.querySelectorAll("[data-option]").forEach(function (button) {
      button.addEventListener("click", function () {
        const type = button.dataset.option;
        const value = button.dataset.value;
        state[type] = state[type] === value ? "" : value;

        variantRoot
          .querySelectorAll('[data-option="' + type + '"]')
          .forEach(function (other) {
            other.classList.toggle(
              "is-active",
              other.dataset.value === state[type]
            );
          });

        apply(findVariant());
      });
    });

    apply(findVariant());
  }

  // ---- Cart badge refresh after an htmx add ------------------------------
  document.body.addEventListener("cart:updated", function (event) {
    const badge = document.getElementById("cart-count");
    if (badge && event.detail && typeof event.detail.count !== "undefined") {
      badge.textContent = event.detail.count;
      badge.classList.toggle("d-none", !event.detail.count);
    }
  });

  // ---- Horizontal product rails ------------------------------------------
  // Scrolling itself is native (overflow-x + scroll-snap), so touch and
  // trackpad work with zero JavaScript. This only drives the arrow buttons
  // and hides them when there is nothing left to scroll toward.
  (function () {
    document.querySelectorAll("[data-rail-wrap]").forEach(function (wrap) {
      var rail = wrap.querySelector("[data-rail]");
      var prev = wrap.querySelector("[data-rail-prev]");
      var next = wrap.querySelector("[data-rail-next]");
      if (!rail || !prev || !next) return;

      function refresh() {
        var maxScroll = rail.scrollWidth - rail.clientWidth;
        var atStart = rail.scrollLeft <= 4;
        var atEnd = rail.scrollLeft >= maxScroll - 4;
        var scrollable = maxScroll > 8;

        prev.dataset.enabled = String(scrollable && !atStart);
        next.dataset.enabled = String(scrollable && !atEnd);
        prev.tabIndex = scrollable && !atStart ? 0 : -1;
        next.tabIndex = scrollable && !atEnd ? 0 : -1;
      }

      function page(direction) {
        // Move by just under a viewport so a card stays partly visible --
        // that peek is what tells people there is more to the right.
        rail.scrollBy({ left: direction * rail.clientWidth * 0.85, behavior: "smooth" });
      }

      prev.addEventListener("click", function () { page(-1); });
      next.addEventListener("click", function () { page(1); });
      rail.addEventListener("scroll", refresh, { passive: true });
      window.addEventListener("resize", refresh);

      // scrollWidth moves twice after first paint: lazy images resolve, and
      // the staggered entrance transform settles. Measure again after both.
      window.setTimeout(refresh, 250);
      window.setTimeout(refresh, 1400);
      rail.addEventListener("transitionend", refresh);
      refresh();
    });
  })();

  // ---- Header condenses once you scroll past the announcement bar --------
  (function () {
    var header = document.querySelector(".site-header");
    if (!header) return;

    var ticking = false;
    function update() {
      header.classList.toggle("is-stuck", window.scrollY > 60);
      ticking = false;
    }
    window.addEventListener("scroll", function () {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(update);   // one class change per frame, at most
    }, { passive: true });
    update();
  })();

  // ---- Cart drawer -------------------------------------------------------
  // After an HTMX add-to-cart the server returns a marker element; that opens
  // the drawer and tells it to reload its contents.
  (function () {
    var drawerEl = document.getElementById("cartDrawer");
    if (!drawerEl || !window.bootstrap) return;

    var drawer = bootstrap.Offcanvas.getOrCreateInstance(drawerEl);

    document.body.addEventListener("htmx:afterSwap", function () {
      var marker = document.querySelector("[data-open-cart-drawer]");
      if (!marker) return;
      marker.remove();

      // Reloads #cart-drawer-body, which listens for this event.
      document.body.dispatchEvent(new CustomEvent("cart-changed"));
      drawer.show();
    });

    // Mirror the header count onto the mobile bar, so the two never disagree.
    var header = document.getElementById("cart-count");
    if (header && window.MutationObserver) {
      new MutationObserver(function () {
        var count = header.textContent.trim();
        document.querySelectorAll("[data-cart-count]").forEach(function (el) {
          el.textContent = count;
          el.classList.toggle("d-none", !count || count === "0");
        });
      }).observe(header, { childList: true, characterData: true, subtree: true });
    }
  })();

  // ---- Theme toggle ------------------------------------------------------
  // The initial theme is resolved by the inline script in <head>; this only
  // handles switching and remembering the choice.
  (function () {
    var root = document.documentElement;
    var toggles = document.querySelectorAll("[data-theme-toggle]");
    if (!toggles.length) return;

    function paintIcons() {
      var dark = root.getAttribute("data-bs-theme") === "dark";
      document.querySelectorAll("[data-theme-icon-light]").forEach(function (el) {
        el.classList.toggle("d-none", dark);
      });
      document.querySelectorAll("[data-theme-icon-dark]").forEach(function (el) {
        el.classList.toggle("d-none", !dark);
      });
    }

    toggles.forEach(function (button) {
      button.addEventListener("click", function () {
        var next = root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
        root.setAttribute("data-bs-theme", next);
        try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
        paintIcons();
      });
    });

    paintIcons();
  })();

  // ---- Scroll reveal -----------------------------------------------------
  // Reveals each element once, then stops observing it. Uses
  // IntersectionObserver rather than a scroll listener so nothing runs on the
  // main thread between intersections.
  (function () {
    var root = document.documentElement;
    if (!root.classList.contains("js-anim")) return;

    var targets = document.querySelectorAll(".reveal, .reveal-stagger");
    if (!targets.length) return;

    function revealAll() {
      targets.forEach(function (el) { el.classList.add("is-revealed"); });
    }

    // Old browser, or the user asked for reduced motion after load: show
    // everything immediately rather than leaving it hidden.
    if (!("IntersectionObserver" in window)) {
      revealAll();
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target;

          // Number the children so CSS can stagger their transition-delay.
          if (el.classList.contains("reveal-stagger")) {
            Array.prototype.forEach.call(el.children, function (child, index) {
              child.style.setProperty("--stagger-index", Math.min(index, 10));
            });
          }

          el.classList.add("is-revealed");
          observer.unobserve(el);
        });
      },
      // Fire slightly before the element reaches the viewport, so the motion
      // has finished by the time it is properly in view.
      { threshold: 0.08, rootMargin: "0px 0px -40px 0px" }
    );

    targets.forEach(function (el) { observer.observe(el); });

    // Safety net: anything still hidden after 3s gets shown regardless.
    window.setTimeout(function () {
      targets.forEach(function (el) {
        if (!el.classList.contains("is-revealed")) el.classList.add("is-revealed");
      });
    }, 3000);
  })();

  // ---- Cart badge bump ----------------------------------------------------
  // A brief pop when the badge count changes, so an HTMX add-to-cart is
  // noticeable without a page reload.
  (function () {
    var badge = document.getElementById("cart-count");
    if (!badge || !window.MutationObserver) return;

    new MutationObserver(function () {
      badge.classList.remove("is-bumped");
      void badge.offsetWidth;              // force reflow so the animation replays
      badge.classList.add("is-bumped");
    }).observe(badge, { childList: true, characterData: true, subtree: true });
  })();

  // ---- Auto-submit filter form -------------------------------------------
  document.querySelectorAll("[data-autosubmit]").forEach(function (element) {
    element.addEventListener("change", function () {
      element.closest("form").requestSubmit();
    });
  });
})();
