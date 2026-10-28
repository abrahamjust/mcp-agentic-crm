const form = document.querySelector("#lead-form");
const errorBox = document.querySelector("#form-error");
const successBox = document.querySelector("#form-success");
const submitButton = document.querySelector("#submit-button");
const optionalTextFields = ["phone", "product_interest", "notes"];

function readFormValue(name) {
  return form.elements.namedItem(name).value.trim();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;

  if (!form.reportValidity()) return;

  // Filled in by the prospective learner, so it is a website enquiry, not a
  // staff entry. Visits / time on site are left out: the client site's
  // tracking script would supply them once the form is embedded there.
  const body = {
    name: readFormValue("name"),
    email: readFormValue("email"),
    source: readFormValue("source") || null,
    lead_origin: "Landing Page Submission",
    occupation: readFormValue("occupation") || null,
    consent: { marketing: form.elements.namedItem("marketing_consent").checked },
  };

  optionalTextFields.forEach((field) => {
    body[field] = readFormValue(field) || null;
  });

  submitButton.disabled = true;
  submitButton.querySelector("span").textContent = "Sending…";
  try {
    const response = await fetch("/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try {
        const result = await response.json();
        message = typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail ?? result);
      } catch {
        // Keep the HTTP status as the visible error when the response is not JSON.
      }
      throw new Error(message);
    }
    await response.json();
    form.hidden = true;
    successBox.hidden = false;
    successBox.scrollIntoView({ block: "nearest" });
  } catch (error) {
    errorBox.textContent = `Your enquiry could not be sent: ${error.message}`;
    errorBox.hidden = false;
    submitButton.disabled = false;
    submitButton.querySelector("span").textContent = "Send enquiry";
  }
});
