(function () {
  var form = document.getElementById("withdraw-bank-form");
  if (!form) {
    return;
  }

  var origenSelect = form.querySelector("#id_origen_fondos");
  var otroWrap = document.getElementById("origen-otro-wrap");
  var otroInput = form.querySelector("#id_origen_fondos_otro");

  function toggleOtro() {
    var show = origenSelect && origenSelect.value === "OTRO";
    if (otroWrap) {
      otroWrap.hidden = !show;
    }
    if (otroInput) {
      otroInput.required = show;
      if (!show) {
        otroInput.setCustomValidity("");
      }
    }
  }

  if (origenSelect) {
    origenSelect.addEventListener("change", toggleOtro);
    toggleOtro();
  }

  form.addEventListener("submit", function () {
    if (origenSelect && origenSelect.value === "OTRO" && otroInput && !otroInput.value.trim()) {
      otroInput.setCustomValidity("Especifica el origen de fondos.");
    } else if (otroInput) {
      otroInput.setCustomValidity("");
    }
  });
})();
