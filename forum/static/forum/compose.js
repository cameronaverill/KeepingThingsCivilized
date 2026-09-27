/* Live character counter for the message box and the proposition box (step 7b).
 *
 * It counts exactly like the server (forum/limits.py: count_message_chars): line endings become "\n", the text is put
 * in Unicode NFC form, leading and trailing whitespace is stripped (the same set of characters Python's str.strip()
 * removes), and the remaining code points are counted (an emoji is 1).
 *
 * It only advises. The button stays enabled, nothing is cut, there is no maxlength; the server decides and explains.
 * Markup: <textarea data-counter="<id of the counter element>" data-limit="3000" data-noun="message"
 *   data-advice="<id of an empty advice element>">; the counter element holds <span data-count>.
 */
(function () {
  "use strict";

  // The characters Python's str.strip() removes: str.isspace() is true for them.
  var PY_SPACE = "\\t\\n\\u000b\\f\\r\\u001c-\\u001f \\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  var LEADING = new RegExp("^[" + PY_SPACE + "]+");
  var TRAILING = new RegExp("[" + PY_SPACE + "]+$");

  function countMessageChars(text) {
    var normalized = String(text).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    if (typeof normalized.normalize === "function") {
      normalized = normalized.normalize("NFC");
    }
    normalized = normalized.replace(LEADING, "").replace(TRAILING, "");
    return Array.from(normalized).length;
  }

  function format(n) {
    return Number(n).toLocaleString("en-US");
  }

  function setup(box) {
    var limit = parseInt(box.getAttribute("data-limit"), 10);
    var counter = document.getElementById(box.getAttribute("data-counter"));
    if (!counter || !limit) { return; }
    var number = counter.querySelector("[data-count]");
    var advice = document.getElementById(box.getAttribute("data-advice") || "");
    var noun = box.getAttribute("data-noun") || "message";

    function update() {
      var n = countMessageChars(box.value);
      if (number) { number.textContent = format(n); }
      var over = n > limit;
      counter.classList.toggle("is-over", over);
      box.classList.toggle("is-over", over);
      if (over) { box.setAttribute("aria-invalid", "true"); } else { box.removeAttribute("aria-invalid"); }
      if (advice) {
        if (over) {
          advice.textContent = "Your " + noun + " is " + format(n) + " characters; the limit is " + format(limit) +
            ". Please shorten it by " + format(n - limit) + " characters. You can still press the button; " +
            "the site will explain if it cannot send it.";
        } else {
          advice.textContent = "";
        }
        advice.hidden = !over;
      }
    }

    box.addEventListener("input", update);
    update();
  }

  // --- The check before posting (step 19) ---------------------------------------------------------------------------
  //
  // Only when the composer form says data-preview="on" (the server decides; "off" or a missing attribute means the form
  // is an ordinary form and nothing is intercepted). Pressing Post first sends the draft to data-check-url and waits up to
  // data-check-timeout MILLISECONDS (rendered from settings.PREVIEW_CLIENT_TIMEOUT_SECONDS). The reply decides:
  //   refused                  -> show the server's message in the alert area, keep the text, post nothing;
  //   concern                  -> show the panel with the moderator's notes; "Post as written" submits the form with the
  //                               hidden check_id, "Edit my message" goes back to the text box (and tells the server);
  //   anything else (no_concern, unavailable, timeout, network error, unexpected reply) -> submit the form for real,
  //                               adding check_id when the reply carried one. A failed check never blocks posting.
  var CHECKING = "Checking your message\u2026";
  var CONCERN_ANNOUNCEMENT = "The moderator would reply to this message.";

  function setupCheck(form) {
    if (form.getAttribute("data-preview") !== "on") { return; }
    var box = form.querySelector("textarea[name=text]");
    var button = form.querySelector("#post-button");
    var status = form.querySelector("#check-status");
    var refusal = form.querySelector("#check-refusal");
    var panel = form.querySelector("#concern-panel");
    var heading = form.querySelector("#concern-heading");
    var notes = form.querySelector("#concern-notes");
    var editButton = form.querySelector("#concern-edit");
    var postButton = form.querySelector("#concern-post");
    var oldError = form.querySelector("#message-error");
    var url = form.getAttribute("data-check-url");
    var timeoutMs = parseInt(form.getAttribute("data-check-timeout"), 10) || 25000;
    if (!box || !button || !url) { return; }
    var label = button.textContent;
    var busy = false;
    var currentCheck = null;

    function token() {
      var field = form.querySelector("input[name=csrfmiddlewaretoken]");
      return field ? field.value : "";
    }

    function say(text) { if (status) { status.textContent = text; } }

    function reset() {
      busy = false;
      button.disabled = false;
      button.textContent = label;
    }

    function submitForReal(checkId) {
      var old = form.querySelector("input[name=check_id]");
      if (old) { old.remove(); }
      if (checkId !== null && checkId !== undefined && checkId !== "") {
        var hidden = document.createElement("input");
        hidden.setAttribute("type", "hidden");
        hidden.setAttribute("name", "check_id");
        hidden.setAttribute("value", String(checkId));
        form.appendChild(hidden);
      }
      form.submit();  // the browser's own submit does not fire the submit event again
    }

    function showRefusal(message) {
      reset();
      say("");
      if (refusal) {
        refusal.textContent = message;
        refusal.hidden = false;
      }
    }

    function showConcern(list, checkId) {
      currentCheck = checkId;
      notes.textContent = "";
      for (var i = 0; i < list.length; i++) {
        var quote = document.createElement("blockquote");
        quote.className = "quote";
        quote.textContent = String(list[i]);
        notes.appendChild(quote);
      }
      button.textContent = label;  // the button stays disabled until the person chooses
      panel.hidden = false;
      say(CONCERN_ANNOUNCEMENT);
      heading.focus();
    }

    function handle(data) {
      if (data && data.status === "refused" && typeof data.message === "string") {
        showRefusal(data.message);
      } else if (data && data.status === "concern" && Array.isArray(data.notes) && data.notes.length > 0 && panel && heading && notes) {
        showConcern(data.notes, data.check_id === undefined ? null : data.check_id);
      } else {
        submitForReal(data && typeof data === "object" ? data.check_id : null);
      }
    }

    function start() {
      busy = true;
      button.disabled = true;
      button.textContent = CHECKING;
      say(CHECKING);
      if (refusal) { refusal.hidden = true; refusal.textContent = ""; }
      if (oldError) { oldError.hidden = true; }
      if (panel) { panel.hidden = true; }
      var finished = false;
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      function finish(data) {
        if (finished) { return; }
        finished = true;
        window.clearTimeout(timer);
        handle(data);
      }
      var timer = window.setTimeout(function () {
        if (controller) { try { controller.abort(); } catch (e) { /* ignored */ } }
        finish(null);
      }, timeoutMs);
      var options = {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": token(),
          "Accept": "application/json"
        },
        body: "text=" + encodeURIComponent(box.value) + "&csrfmiddlewaretoken=" + encodeURIComponent(token())
      };
      if (controller) { options.signal = controller.signal; }
      fetch(url, options).then(function (response) {
        if (!response.ok) { throw new Error("status " + response.status); }
        return response.json();
      }).then(finish, function () { finish(null); });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!busy) { start(); }
    });

    if (postButton) {
      postButton.addEventListener("click", function () { submitForReal(currentCheck); });
    }
    if (editButton) {
      editButton.addEventListener("click", function () {
        var checkId = currentCheck;
        panel.hidden = true;
        notes.textContent = "";
        say("");
        reset();
        box.focus();
        if (checkId !== null && checkId !== undefined) {
          try {  // told in the background; a failure is ignored
            fetch(url + encodeURIComponent(checkId) + "/edit/", {
              method: "POST",
              credentials: "same-origin",
              headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": token() },
              body: "csrfmiddlewaretoken=" + encodeURIComponent(token())
            }).then(function () {}, function () {});
          } catch (e) { /* ignored */ }
        }
        currentCheck = null;
      });
    }
    // Coming back to the page from the browser's history: the button must not stay stuck on "Checking".
    window.addEventListener("pageshow", function (event) {
      if (event && event.persisted) { reset(); say(""); if (panel) { panel.hidden = true; } }
    });
  }

  var composers = document.querySelectorAll("form[data-preview]");
  for (var c = 0; c < composers.length; c++) { setupCheck(composers[c]); }

  var boxes = document.querySelectorAll("textarea[data-counter]");
  for (var i = 0; i < boxes.length; i++) { setup(boxes[i]); }

  // Exposed for a quick check in a console or a test; nothing on the page depends on it.
  window.forumCountMessageChars = countMessageChars;
})();
