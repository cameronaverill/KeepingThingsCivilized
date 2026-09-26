/* Keeps the conversation page up to date (step 7b).
 *
 * Every data-poll-seconds (rendered from settings.POLL_SECONDS) it asks the messages endpoint for messages newer than
 * the last one it has, appends them (never twice: each is keyed by its seq_no), updates the message count and the
 * moderation notice, and stops when the conversation is closed. After an error it waits longer and longer (up to a
 * minute) and returns to the normal pace on the first success. It never touches the compose box, so what someone is
 * typing is not disturbed; if the state of the conversation changes (someone joined, someone ended it) it reloads the
 * page only when nothing is being typed, otherwise it shows a notice with a reload link.
 */
(function () {
  "use strict";

  var thread = document.getElementById("thread");
  if (!thread) { return; }

  var url = thread.getAttribute("data-poll-url");
  var base = Math.max(1, parseFloat(thread.getAttribute("data-poll-seconds")) || 3) * 1000;
  var lastSeq = parseInt(thread.getAttribute("data-last-seq"), 10) || 0;
  var initial = {
    status: thread.getAttribute("data-status"),
    waiting: thread.getAttribute("data-waiting") === "true",
    canPost: thread.getAttribute("data-can-post") === "true"
  };
  var list = document.getElementById("message-list");
  var notice = document.getElementById("moderation-notice");
  var changed = document.getElementById("state-changed");
  var box = document.getElementById("message");
  var delay = base;
  var timer = null;
  var stopped = false;

  function seen(seq) {
    return !!list.querySelector('[data-seq="' + seq + '"]');
  }

  function append(message) {
    if (!message || typeof message.seq_no !== "number" || typeof message.html !== "string") { return; }
    if (seen(message.seq_no)) { return; }
    var placeholder = document.getElementById("no-messages");
    if (placeholder) { placeholder.remove(); }
    // The html is the server's own rendering of the message partial (already escaped).
    list.insertAdjacentHTML("beforeend", message.html);
    if (message.seq_no > lastSeq) { lastSeq = message.seq_no; }
  }

  function updateNotice(text) {
    if (!notice) { return; }
    var target = notice.querySelector("[data-notice-text]");
    if (target) { target.textContent = text || ""; }
    notice.hidden = !text;
  }

  function stateChanged(data) {
    return data.status !== initial.status || !!data.waiting !== initial.waiting || !!data.can_post !== initial.canPost;
  }

  function apply(data) {
    var messages = Array.isArray(data.messages) ? data.messages : [];
    for (var i = 0; i < messages.length; i++) { append(messages[i]); }
    var count = document.querySelector("[data-message-count]");
    if (count && typeof data.message_count === "number") { count.textContent = String(data.message_count); }
    updateNotice(data.moderation_notice);
    if (stateChanged(data)) {
      stopped = true;
      if (!box || box.value.trim() === "") {
        window.location.reload();
      } else if (changed) {
        changed.hidden = false;
      }
      return;
    }
    if (data.status === "closed") { stopped = true; }
  }

  function schedule() {
    if (stopped) { return; }
    var wait = document.hidden ? Math.max(delay, 15000) : delay;
    timer = window.setTimeout(poll, wait);
  }

  function poll() {
    fetch(url + "?after=" + encodeURIComponent(lastSeq), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "manual",
      headers: { "Accept": "application/json" }
    }).then(function (response) {
      if (!response.ok) { throw new Error("status " + response.status); }
      return response.json();
    }).then(function (data) {
      delay = base;
      apply(data);
      schedule();
    }).catch(function () {
      delay = Math.min(delay * 2, 60000);
      schedule();
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && !stopped) {
      window.clearTimeout(timer);
      timer = window.setTimeout(poll, 500);
    }
  });

  schedule();
})();
