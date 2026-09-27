(function () {
  var config = window.VERSO_WITHDRAW_QUOTE;
  if (!config || !config.rateCompra) {
    return;
  }

  var input = document.getElementById("id_amount_usdc");
  var sendEl = document.getElementById("quote-send-usdc");
  var receiveEl = document.getElementById("quote-receive-fiat");
  if (!input || !sendEl || !receiveEl) {
    return;
  }

  var rateCompra = Number(config.rateCompra);
  var minUsdc = Number(config.minUsdc || 0.0000001);
  var fiatSymbol = config.fiatSymbol || "S/";

  function parseAmount(raw) {
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

  function formatFiat(value) {
    return (
      fiatSymbol +
      " " +
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
    sendEl.textContent = "— USDC";
    receiveEl.textContent = fiatSymbol + " —";
  }

  function updateQuote() {
    var amount = parseAmount(input.value);
    if (amount === null || amount < minUsdc) {
      resetQuote();
      return;
    }
    sendEl.textContent = formatUsdc(amount);
    receiveEl.textContent = formatFiat(amount * rateCompra);
  }

  input.addEventListener("input", updateQuote);
  input.addEventListener("change", updateQuote);
  updateQuote();
})();
