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

  var boxes = document.querySelectorAll("textarea[data-counter]");
  for (var i = 0; i < boxes.length; i++) { setup(boxes[i]); }

  // Exposed for a quick check in a console or a test; nothing on the page depends on it.
  window.forumCountMessageChars = countMessageChars;
})();
