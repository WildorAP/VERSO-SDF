(function () {
  // "Hacer otra operación": only the page that opened this popup (versotek.io) holds the SEP-10
  // token needed to start a new SEP-24 transaction, so the button asks it to do so.
  var root = document.getElementById("new-operation");
  if (!root) {
    return;
  }
  var opener = window.opener;
  if (!opener || opener === window) {
    root.remove();
    return;
  }

  var button = document.getElementById("new-operation-btn");

  window.versoRevealNewOperation = function () {
    root.classList.remove("is-hidden");
  };

  button.addEventListener("click", function () {
    button.disabled = true;
    button.textContent = "Abriendo nueva operación…";
    opener.postMessage(
      { type: "verso:new_operation", operation: root.dataset.operation },
      "*"
    );
  });
})();
