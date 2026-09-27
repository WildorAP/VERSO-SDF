(function () {
  var root = document.getElementById("deposit-wait-poll-root");
  if (!root) {
    return;
  }

  var pollUrl = root.dataset.pollUrl;
  var currentStatus = root.dataset.status || "";
  var amountUsdc = root.dataset.amountUsdc || "";
  var statusEl = document.getElementById("deposit-wait-status");
  var headingEl = document.getElementById("deposit-wait-heading");
  var ledeEl = document.getElementById("deposit-wait-lede");
  var completedDateRow = document.getElementById("deposit-completed-date-row");
  var completedDateEl = document.getElementById("deposit-completed-date");
  var txidBlock = document.getElementById("deposit-wait-txid-block");
  var txidEl = document.getElementById("stellar-txid");
  var txidLink = document.getElementById("stellar-txid-link");
  var pollIntervalMs = 4000;
  var timerId = null;

  var STATUS_MESSAGES = {
    pending_user_transfer_start: "Verificando depósito en soles…",
    pending_anchor: "Depósito confirmado. Enviando USDC a tu wallet…",
    pending_stellar: "Enviando USDC on-chain…",
    pending_external: "Procesando depósito…",
  };

  function notifyWallet(transactionPayload) {
    if (!transactionPayload) {
      return;
    }
    var targetWindow = window.opener || window.parent;
    if (!targetWindow || targetWindow === window) {
      return;
    }
    targetWindow.postMessage({ transaction: transactionPayload }, "*");
  }

  function stopPolling() {
    if (timerId !== null) {
      window.clearInterval(timerId);
      timerId = null;
    }
  }

  function reveal(el) {
    if (el) {
      el.classList.remove("is-hidden");
    }
  }

  function showTxid(stellarTransactionId, stellarTxUrl) {
    if (!stellarTransactionId || !txidBlock || !txidEl) {
      return;
    }
    txidEl.textContent = stellarTransactionId;
    reveal(txidBlock);
    if (txidLink && stellarTxUrl) {
      txidLink.href = stellarTxUrl;
      reveal(txidLink);
    }
  }

  function showCompleted(payload) {
    if (headingEl) {
      headingEl.textContent = "Operación finalizada";
    }
    if (ledeEl && amountUsdc) {
      ledeEl.innerHTML =
        "Enviamos <strong>" + amountUsdc + " USDC</strong> a tu wallet Stellar.";
    }
    if (statusEl) {
      statusEl.textContent = "Operación finalizada";
      statusEl.classList.add("didit-status--success");
      statusEl.classList.remove("didit-status--error");
    }
    if (completedDateEl && payload.completed_at_display) {
      completedDateEl.textContent = payload.completed_at_display;
    }
    reveal(completedDateRow);
    showTxid(payload.stellar_transaction_id, payload.stellar_tx_url);
    notifyWallet(payload.transaction);
    stopPolling();
  }

  function showError(payload) {
    if (statusEl) {
      statusEl.textContent =
        payload.message ||
        "Hubo un problema con tu depósito. Escríbenos a soporte@versotek.io.";
      statusEl.classList.add("didit-status--error");
      statusEl.classList.remove("didit-status--success");
    }
    stopPolling();
  }

  function poll() {
    fetch(pollUrl, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("poll failed");
        }
        return response.json();
      })
      .then(function (payload) {
        if (!payload || !payload.status) {
          return;
        }

        if (payload.status === "completed") {
          showCompleted(payload);
          return;
        }

        if (payload.terminal || payload.status === "error") {
          showError(payload);
          return;
        }

        var message =
          STATUS_MESSAGES[payload.status] || "Procesando depósito…";
        if (statusEl && statusEl.textContent !== message) {
          statusEl.textContent = message;
        }
        currentStatus = payload.status;
      })
      .catch(function () {});
  }

  if (currentStatus === "completed" || currentStatus === "error") {
    return;
  }

  timerId = window.setInterval(poll, pollIntervalMs);
  poll();
})();
