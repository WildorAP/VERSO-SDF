(function () {
  document.querySelectorAll(".btn-copy").forEach(function (button) {
    button.addEventListener("click", function () {
      var targetId = button.getAttribute("data-copy-target");
      var target = targetId ? document.getElementById(targetId) : null;
      var text = button.getAttribute("data-copy-value") || (target ? target.textContent.trim() : "");
      if (!text) {
        return;
      }
      navigator.clipboard
        .writeText(text)
        .then(function () {
          var original = button.textContent;
          button.textContent = "Copiado";
          window.setTimeout(function () {
            button.textContent = original;
          }, 1600);
        })
        .catch(function () {
          window.prompt("Copia este valor:", text);
        });
    });
  });
})();
