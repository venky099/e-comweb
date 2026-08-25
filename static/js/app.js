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

  // ---- Auto-submit filter form -------------------------------------------
  document.querySelectorAll("[data-autosubmit]").forEach(function (element) {
    element.addEventListener("change", function () {
      element.closest("form").requestSubmit();
    });
  });
})();
