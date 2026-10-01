/* "Provide factual background" button (step 20b): the click on an eligible act (offer_research,
 * correct_factual_error, or provide_information -- forum/viewmodels.py's RESEARCH_ELIGIBLE_ACT_TYPES).
 *
 * The button lives inside a tiny form (`.research-form`), server-rendered by forum/_message.html, both on first load
 * and inside the HTML poll.js appends for a new moderator message; a delegated listener on `document` picks up both
 * without any per-element setup. On submit: intercept it, replace the form with the same "pending" markup the server
 * itself renders once a research run exists (an optimistic update, shown before the response comes back), then POST
 * to the endpoint. poll.js's own ~3s polling is unchanged: it already appends the resulting moderator note once
 * posted, so nothing here waits for or triggers a poll. On a failed request the form is put back so the person can
 * try again; nothing here ever mentions cost, spend or budget.
 */
(function () {
  "use strict";

  var PENDING_HTML = '<p class="msg-meta research-pending"><span class="spinner" aria-hidden="true"></span>'
    + '<span>Checking — this may take a moment</span></p>';

  function token(form) {
    var field = form.querySelector("input[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  document.addEventListener("submit", function (event) {
    var form = event.target.closest ? event.target.closest(".research-form") : null;
    if (!form) { return; }
    event.preventDefault();

    // The slot (.research-slot) holds whichever control the state calls for; fall back to the form's parent.
    var holder = form.closest(".research-slot") || form.parentNode;
    var original = holder ? holder.innerHTML : null;
    var originalState = holder && holder.getAttribute ? holder.getAttribute("data-state") : null;
    var action = form.getAttribute("action");
    var csrf = token(form);

    // Optimistic: shown immediately, before the request finishes. "busy" tells poll.js to leave the slot alone.
    if (holder) {
      holder.innerHTML = PENDING_HTML;
      holder.setAttribute("data-state", "pending");
      holder.setAttribute("data-busy", "1");
    }

    fetch(action, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": csrf,
        "Accept": "application/json"
      },
      body: "csrfmiddlewaretoken=" + encodeURIComponent(csrf)
    }).then(function (response) {
      if (!response.ok) { throw new Error("status " + response.status); }
      return response.json();
    }).then(function () {
      // Accepted: the state stays "pending"; poll.js takes over once the slot is no longer busy.
      if (holder) { holder.removeAttribute("data-busy"); }
    }).catch(function () {
      // Something went wrong on our side: put the control back so the person can try again.
      if (holder) {
        if (original !== null) { holder.innerHTML = original; }
        if (originalState !== null) { holder.setAttribute("data-state", originalState); }
        holder.removeAttribute("data-busy");
      }
    });
  });
})();
