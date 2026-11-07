(function () {
  // "Firmar y enviar pago": the wallet connection lives in the page that opened this popup
  // (versotek.io), so the button asks it to sign. External wallets never answer the check, so
  // they keep paying by themselves and the button stays hidden.
  var root = document.getElementById("wallet-pay");
  var opener = window.opener;
  if (!root || !opener || opener === window) {
    return;
  }

  var transactionId = root.dataset.transactionId;
  var button = document.getElementById("wallet-pay-btn");
  var statusEl = document.getElementById("wallet-pay-status");
  var ledeEl = document.getElementById("wallet-pay-lede");
  var BUTTON_LABEL = button.textContent;

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.remove("is-hidden");
    statusEl.classList.toggle("didit-status--error", isError);
    statusEl.classList.toggle("didit-status--success", !isError);
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (event.source !== opener || !data || data.transaction_id !== transactionId) {
      return;
    }
    if (data.type === "verso:withdraw_pay_ready") {
      root.classList.remove("is-hidden");
      if (ledeEl) {
        ledeEl.textContent = "Presiona el botón y firma el pago en tu wallet.";
      }
    } else if (data.type === "verso:withdraw_pay_result") {
      if (data.ok) {
        button.remove();
        showStatus("Pago enviado. Estamos verificando tu USDC…", false);
      } else {
        button.disabled = false;
        button.textContent = BUTTON_LABEL;
        showStatus(data.error || "No se pudo enviar el pago. Inténtalo de nuevo.", true);
      }
    }
  });

  button.addEventListener("click", function () {
    button.disabled = true;
    button.textContent = "Firma en tu wallet…";
    statusEl.classList.add("is-hidden");
    opener.postMessage({ type: "verso:pay_withdraw", transaction_id: transactionId }, "*");
  });

  opener.postMessage({ type: "verso:withdraw_pay_check", transaction_id: transactionId }, "*");
})();
