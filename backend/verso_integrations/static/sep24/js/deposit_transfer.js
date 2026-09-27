(function () {
  document.querySelectorAll(".btn-copy").forEach(function (button) {
    button.addEventListener("click", function () {
      var targetId = button.getAttribute("data-copy-target");
      var target = targetId ? document.getElementById(targetId) : null;
      if (!target) {
        return;
      }
      var text = target.textContent.trim();
      if (!text) {
        return;
      }
      navigator.clipboard.writeText(text).then(function () {
        var original = button.textContent;
        button.textContent = "Copiado";
        window.setTimeout(function () {
          button.textContent = original;
        }, 1600);
      }).catch(function () {
        window.prompt("Copia este valor:", text);
      });
    });
  });

  var fileInput = document.getElementById("id_receipt");
  var filenameEl = document.getElementById("upload-filename");
  var uploadZone = document.getElementById("upload-zone");
  var submitBtn = document.getElementById("transfer-submit");
  var transferForm = document.querySelector(".transfer-form");

  function syncReceiptState() {
    var file = fileInput && fileInput.files && fileInput.files[0];
    if (filenameEl) {
      filenameEl.textContent = file ? file.name : "Toca para elegir archivo";
    }
    if (uploadZone) {
      uploadZone.classList.toggle("is-filled", Boolean(file));
    }
    if (submitBtn) {
      submitBtn.disabled = !file;
    }
  }

  if (fileInput) {
    fileInput.addEventListener("change", syncReceiptState);
    syncReceiptState();
  }

  if (transferForm && fileInput) {
    transferForm.addEventListener("submit", function (event) {
      if (!fileInput.files || !fileInput.files[0]) {
        event.preventDefault();
        fileInput.setCustomValidity("Debes adjuntar la constancia de tu transferencia.");
        fileInput.reportValidity();
        fileInput.setCustomValidity("");
      }
    });
  }
})();
