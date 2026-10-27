const form = document.querySelector("#lead-form");
const errorBox = document.querySelector("#form-error");
const submitButton = document.querySelector("#submit-button");
const optionalTextFields = ["phone", "company", "job_title", "product_interest", "notes"];
const optionalNumberFields = ["total_visits", "time_on_website_sec", "pages_per_visit"];

function readFormValue(name) {
  return form.elements.namedItem(name).value.trim();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;

  if (!form.reportValidity()) return;

  const body = {
    name: readFormValue("name"),
    email: readFormValue("email"),
    company_size: readFormValue("company_size") || null,
    source: readFormValue("source") || null,
    lead_origin: "Lead Add Form",
    occupation: readFormValue("occupation") || null,
    consent: { marketing: form.elements.namedItem("marketing_consent").checked },
  };

  optionalTextFields.forEach((field) => {
    body[field] = readFormValue(field) || null;
  });
  optionalNumberFields.forEach((field) => {
    const value = readFormValue(field);
    body[field] = value === "" ? null : Number(value);
  });

  submitButton.disabled = true;
  submitButton.querySelector("span").textContent = "Saving lead…";
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
    location.assign("/?created=1");
  } catch (error) {
    errorBox.textContent = `The lead could not be saved: ${error.message}`;
    errorBox.hidden = false;
    submitButton.disabled = false;
    submitButton.querySelector("span").textContent = "Save lead to pipeline";
  }
});
