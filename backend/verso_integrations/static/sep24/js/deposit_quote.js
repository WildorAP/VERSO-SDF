(function () {
  var config = window.VERSO_DEPOSIT_QUOTE;
  if (!config || !config.rateVenta) {
    return;
  }

  var input = document.getElementById("id_amount_pen");
  var sendEl = document.getElementById("quote-send-pen");
  var receiveEl = document.getElementById("quote-receive-usdc");
  if (!input || !sendEl || !receiveEl) {
    return;
  }

  var rateVenta = Number(config.rateVenta);
  var minPen = Number(config.minPen || 1);

  function parsePen(raw) {
    var normalized = String(raw || "")
      .trim()
      .replace(/\s/g, "")
      .replace(",", ".");
    if (!normalized) {
      return null;
    }
    var value = Number(normalized);
    if (!Number.isFinite(value) || value <= 0) {
      return null;
    }
    return value;
  }

  function formatPen(value) {
    return (
      "S/ " +
      value.toLocaleString("es-PE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  }

  function formatUsdc(value) {
    var scaled = Math.round(value * 1e7) / 1e7;
    return (
      scaled.toLocaleString("es-PE", {
        minimumFractionDigits: 7,
        maximumFractionDigits: 7,
      }) + " USDC"
    );
  }

  function resetQuote() {
    sendEl.textContent = "S/ —";
    receiveEl.textContent = "— USDC";
  }

  function updateQuote() {
    var pen = parsePen(input.value);
    if (pen === null || pen < minPen) {
      resetQuote();
      return;
    }
    sendEl.textContent = formatPen(pen);
    receiveEl.textContent = formatUsdc(pen / rateVenta);
  }

  input.addEventListener("input", updateQuote);
  input.addEventListener("change", updateQuote);
  updateQuote();
})();
