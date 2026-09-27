(function () {
  var form = document.getElementById("profile-wizard-form");
  if (!form) {
    return;
  }

  var cards = Array.prototype.slice.call(form.querySelectorAll(".wizard-card"));
  var progressBar = document.getElementById("wizard-progress-bar");
  var stepLabel = document.getElementById("wizard-step-label");
  var current = 0;
  var total = cards.length;

  function selectedRadio(name) {
    var input = form.querySelector('input[name="' + name + '"]:checked');
    return input ? input.value : "";
  }

  function toggleConditional(id, show) {
    var node = document.getElementById(id);
    if (node) {
      node.hidden = !show;
    }
  }

  function syncConditionals() {
    toggleConditional("origen-otro-wrap", form.origen_fondos.value === "OTRO");
    toggleConditional("conyuge-wrap", selectedRadio("casado") === "si");
    toggleConditional("pep-notice", selectedRadio("es_pep") === "si");
    toggleConditional("familiar-pep-wrap", selectedRadio("es_familiar_pep") === "si");
  }

  function showStep(index) {
    current = Math.max(0, Math.min(index, total - 1));
    cards.forEach(function (card, i) {
      var active = i === current;
      card.hidden = !active;
      card.classList.toggle("is-active", active);
    });
    if (progressBar) {
      progressBar.style.width = (((current + 1) / total) * 100) + "%";
    }
    if (stepLabel) {
      stepLabel.textContent = "Paso " + (current + 1) + " de " + total;
    }
    syncConditionals();
  }

  function normalizePhoneInput() {
    var input = form.querySelector("#id_celular");
    if (!input) {
      return "+51";
    }
    var digits = input.value.replace(/\D/g, "").slice(0, 9);
    input.value = digits;
    return digits.length === 9 && digits.charAt(0) === "9" ? "+51" + digits : "";
  }

  function validateCurrentStep() {
    var card = cards[current];
    if (!card) {
      return true;
    }
    var required = card.querySelectorAll("input[required], select[required]");
    for (var i = 0; i < required.length; i++) {
      if (!required[i].checkValidity()) {
        required[i].reportValidity();
        return false;
      }
    }
    if (current === 0) {
      var phone = normalizePhoneInput();
      if (!phone) {
        var phoneInput = form.querySelector("#id_celular");
        phoneInput.setCustomValidity("Ingresa un celular peruano válido (9 dígitos).");
        phoneInput.reportValidity();
        phoneInput.setCustomValidity("");
        return false;
      }
    }
    if (current === 2 && form.origen_fondos.value === "OTRO") {
      var otro = form.querySelector("#id_origen_fondos_otro");
      if (otro && !otro.value.trim()) {
        otro.setCustomValidity("Especifica el origen de fondos.");
        otro.reportValidity();
        otro.setCustomValidity("");
        return false;
      }
    }
    if (current === 3 && selectedRadio("casado") === "si") {
      var conyuge = form.querySelector("#id_nombre_conyugue");
      if (conyuge && !conyuge.value.trim()) {
        conyuge.setCustomValidity("Ingresa el nombre de tu cónyuge.");
        conyuge.reportValidity();
        conyuge.setCustomValidity("");
        return false;
      }
    }
    return true;
  }

  form.querySelectorAll(".wizard-next").forEach(function (button) {
    button.addEventListener("click", function () {
      if (!validateCurrentStep()) {
        return;
      }
      showStep(current + 1);
    });
  });

  form.querySelectorAll(".wizard-back").forEach(function (button) {
    button.addEventListener("click", function () {
      showStep(current - 1);
    });
  });

  form.addEventListener("change", syncConditionals);
  form.addEventListener("submit", function (event) {
    syncConditionals();
    var phone = normalizePhoneInput();
    if (phone) {
      var hidden = form.querySelector('input[name="celular_full"]');
      if (!hidden) {
        hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "celular_full";
        form.appendChild(hidden);
      }
      form.celular.value = phone;
    }
    if (selectedRadio("es_familiar_pep") === "si") {
      var nombre = form.querySelector("#id_familiar_pep_nombre");
      var cargo = form.querySelector("#id_familiar_pep_cargo");
      if (!nombre.value.trim() || !cargo.value.trim()) {
        event.preventDefault();
        showStep(4);
        if (!nombre.value.trim()) {
          nombre.setCustomValidity("Ingresa el nombre del familiar PEP.");
          nombre.reportValidity();
          nombre.setCustomValidity("");
        } else {
          cargo.setCustomValidity("Indica el cargo o puesto del familiar PEP.");
          cargo.reportValidity();
          cargo.setCustomValidity("");
        }
      }
    }
  });

  showStep(0);

  var phoneInput = form.querySelector("#id_celular");
  if (phoneInput && phoneInput.value) {
    phoneInput.value = phoneInput.value.replace(/\D/g, "").replace(/^51/, "").slice(0, 9);
  }
})();
