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
  if (fileInput && filenameEl) {
    fileInput.addEventListener("change", function () {
      var file = fileInput.files && fileInput.files[0];
      filenameEl.textContent = file ? file.name : "Toca para elegir archivo";
      if (uploadZone) {
        uploadZone.classList.toggle("is-filled", Boolean(file));
      }
    });
  }
})();
