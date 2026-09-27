(function () {
  var root = document.getElementById("verso-tx-poll-root");
  if (!root) {
    return;
  }

  var pollUrl = root.dataset.pollUrl;
  var currentStatus = root.dataset.status || "";
  var onChangeCallback = root.dataset.onChange || "";
  var pollIntervalMs = 3000;
  var timerId = null;

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

        if (payload.status !== currentStatus) {
          notifyWallet(payload.transaction);
          window.location.reload();
          return;
        }

        if (payload.terminal && timerId !== null) {
          window.clearInterval(timerId);
          timerId = null;
        }
      })
      .catch(function () {});
  }

  if (currentStatus === "completed" || currentStatus === "error") {
    return;
  }

  timerId = window.setInterval(poll, pollIntervalMs);
  poll();
})();
