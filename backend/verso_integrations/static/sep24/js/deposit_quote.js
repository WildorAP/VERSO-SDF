(function () {
  var config = window.VERSO_DEPOSIT_QUOTE;
  if (!config || !config.rateVenta) {
    return;
  }

  var input = document.getElementById("id_amount_fiat");
  var sendEl = document.getElementById("quote-send-fiat");
  var receiveEl = document.getElementById("quote-receive-usdc");
  var minWarningEl = document.getElementById("quote-min-warning");
  var submitEl = document.getElementById("quote-submit");
  if (!input || !sendEl || !receiveEl) {
    return;
  }

  var rateVenta = Number(config.rateVenta);
  var minFiat = Number(config.minFiat || 1);
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
    sendEl.textContent = fiatSymbol + " —";
    receiveEl.textContent = "— USDC";
  }

  function setBelowMinimum(belowMinimum) {
    if (minWarningEl) {
      minWarningEl.textContent = "Monto mínimo: " + formatFiat(minFiat);
      minWarningEl.hidden = !belowMinimum;
    }
    if (submitEl) {
      submitEl.disabled = belowMinimum;
    }
  }

  function updateQuote() {
    var amount = parseAmount(input.value);
    if (amount === null) {
      resetQuote();
      setBelowMinimum(false);
      return;
    }
    sendEl.textContent = formatFiat(amount);
    receiveEl.textContent = formatUsdc(amount / rateVenta);
    setBelowMinimum(amount < minFiat);
  }

  input.addEventListener("input", updateQuote);
  input.addEventListener("change", updateQuote);
  updateQuote();
})();
