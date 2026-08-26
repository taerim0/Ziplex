// The pack job/review flow -- see app.js's header comment for the overall
// module split. This file: renderPackJob() -- kicks off/polls a background
// pack job (gui_server.py's /api/pack*), then the "reviewing" state's full
// correction form (project name/guide/rules/summaries, plus the
// dependency-tree overview + editor from graph.js for relationships)
// before submit/finalize.

import { app, nav, el, api, apiPost, openProject, confidenceLevel } from "./app.js";
import { t } from "./i18n.js";
import { renderDependencyTreeOverview, renderRelationshipEditor } from "./graph.js";

// Navigation guard for in-app hash links (topbar/sidebar) -- router.js's
// document-level click-delegation calls confirmLeaveActivePackJob() before
// letting any `<a href="#...">` click through. A hash-link click doesn't
// fire `beforeunload` (that only ever catches a real page reload/tab
// close), so a pack job's "running"/"reviewing" screen used to have no way
// to warn before a topbar click silently swapped it out with no
// confirmation at all -- reported directly by the user clicking a topbar
// item mid-pack and landing on a different page with zero warning.
//
// The backend job itself is *not* affected either way by this guard, or by
// leaving without it: packager.pack() runs on its own background thread in
// the Flask/pywebview process (see gui/pack_service.py's module docstring),
// entirely decoupled from whether any browser tab happens to be watching
// it. Leaving mid-"running" doesn't stop or lose the pack -- it keeps
// computing regardless. What's actually at risk is *reaching* the result
// afterward: a "reviewing" job with no jobs-list page has nothing bringing
// a human back to its `#/pack/<id>` URL to actually submit/save it, and a
// "reviewing" screen's own name/guide/rules/summary edits (unlike a link/
// unlink, which hits the server immediately) live only in that page's JS
// state until submitted.
let activeGuard = null; // null | () => boolean (return false to block the navigation)

export function confirmLeaveActivePackJob() {
  return activeGuard ? activeGuard() : true;
}

export async function renderPackJob(jobId) {
  // Reset on every fresh render (a brand-new job, or the URL bar navigating
  // directly to one) rather than inheriting whatever a previous job's guard
  // left behind -- that guard belongs to a job this render has nothing to
  // do with.
  activeGuard = null;
  nav.classList.add("hidden");
  app.innerHTML = "";

  const logPre = el("pre", { class: "pack-log" });
  const statusBadge = el("span", { class: "pack-status running", text: t("pack.status.running") });
  const body = el("div");

  // "running" used to have no controls at all -- once a pack started, the
  // only way out was closing the window. request_cancel() (see
  // pack_service.py) lets it stop at its next checkpoint instead: "저장 후
  // 취소" checkpoints wherever analysis has gotten to (a later pack on the
  // same project auto-resumes from it, same as a failed one already would),
  // "그냥 취소" discards it. Hidden once the job leaves "running" (see
  // poll() below) -- nothing left running to stop by then.
  const stopSaveButton = el("button", { class: "secondary", text: t("pack.stopSave") });
  const stopDiscardButton = el("button", { class: "secondary", text: t("pack.stopDiscard") });
  const stopRow = el("div", { class: "copy-row" }, [stopSaveButton, stopDiscardButton]);

  async function requestStop(save) {
    stopSaveButton.disabled = true;
    stopDiscardButton.disabled = true;
    try {
      await apiPost("/api/pack/stop", { job_id: jobId, save });
    } catch (e) {
      // best-effort -- if the job already left "running" (finished or
      // failed on its own just before this landed), the next poll tick
      // already shows whatever it actually ended up as.
    }
  }
  // Guards a topbar/sidebar click while the job is still "running" -- see
  // confirmLeaveActivePackJob()'s own comment above for why this exists.
  // Both confirm()s are synchronous/blocking, so by the time this returns
  // the human has already answered both; requestStop(true) is fired
  // without awaiting it (same "best-effort" spirit as its own button
  // handlers below) since navigation proceeds regardless of whether that
  // request has landed yet -- it'll complete server-side either way.
  // (Re)armed inside poll() itself, only once a response actually confirms
  // the job is still "running"/"finalizing" -- not set eagerly here, so a
  // click in the brief window before the first poll response lands (or a
  // reload of a job that already finished/errored server-side) never asks
  // about a job that isn't actually running anymore, a real gap code
  // review caught in this function's first version.
  function armRunningGuard() {
    activeGuard = () => {
      if (!confirm(t("pack.guard.confirmLeaveRunning"))) return false;
      if (confirm(t("pack.guard.offerStopAndSave"))) requestStop(true);
      // Leaving is confirmed either way past this point -- clear the guard
      // so it doesn't keep firing for every later, unrelated navigation
      // while the job goes on running in the background with nobody
      // watching (also a real bug code review caught: declining "stop and
      // save" left this closure armed, popping the same "still running?"
      // prompt on every subsequent click anywhere in the app until the job
      // actually finished). Also stop this render's own poll() loop --
      // without this, its already-scheduled setTimeout would fire ~1s
      // later, see the job is still "running" server-side, and call
      // armRunningGuard() again, silently re-arming the very guard just
      // cleared and reintroducing the same bug on a delay.
      activeGuard = null;
      stopped = true;
      return true;
    };
  }

  stopSaveButton.addEventListener("click", () => {
    if (confirm(t("pack.confirmStopSave"))) requestStop(true);
  });
  stopDiscardButton.addEventListener("click", () => {
    if (confirm(t("pack.confirmStopDiscard"))) requestStop(false);
  });

  const card = el("div", { class: "card" }, [
    el("h1", { text: t("pack.title") }),
    statusBadge,
    stopRow,
    el("h3", { text: t("pack.log") }),
    logPre,
    body,
  ]);
  app.appendChild(card);

  // retryParams (pack_service.get_job_status()'s own shape -- project_path/
  // output_path/no_cache/no_llm/selected_files) lets a repeated-LLM-failure
  // error screen offer a real way forward instead of a dead end: reposting
  // it verbatim to /api/pack picks up from pack()'s own checkpoint
  // (non-interactive resume is always-yes -- see checkpoint.
  // resume_checkpoint_choice()) instead of a human having to reselect
  // files/retype paths from scratch. Omitted (undefined) when the error
  // came from somewhere that never got as far as having job state to read
  // retry_params from (the status/review fetch itself failing) -- the
  // "back to the pack form" button below still gives a way out either way.
  function showErrorState(message, retryParams) {
    activeGuard = null; // nothing left running/unsaved to warn about
    statusBadge.className = "pack-status error";
    statusBadge.textContent = t("pack.status.error");

    const backButton = el("button", { class: "secondary", text: t("pack.backToPackForm"), onclick: () => {
      location.hash = "#/pack";
    } });
    const buttons = [backButton];

    if (retryParams) {
      const retryButton = el("button", { text: t("pack.retry"), onclick: async () => {
        retryButton.disabled = true;
        try {
          const { job_id } = await apiPost("/api/pack", retryParams);
          location.hash = `#/pack/${job_id}`;
        } catch (e) {
          retryButton.disabled = false;
          alert(e.message);
        }
      } });
      buttons.unshift(retryButton);
    }

    body.appendChild(el("div", { class: "error", text: message }));
    body.appendChild(el("div", { class: "copy-row" }, buttons));
  }

  function showDoneState(result) {
    activeGuard = null; // saved already -- nothing left to warn about leaving
    statusBadge.className = "pack-status done";
    statusBadge.textContent = t("pack.status.done");
    const openIt = () => openProject(result.aif_path, result.project_path);
    body.appendChild(el("div", { class: "copy-row" }, [
      el("p", { text: t("pack.saved", { path: result.aif_path }) }),
      el("button", { text: t("pack.openResult"), onclick: openIt }),
    ]));
  }

  // one summary editor row, shared between the "needs review" (flagged,
  // shown with its real signatures so a human can judge the mismatch
  // without opening the file -- same info corrector.py prints to a
  // terminal) and "auto kept" (collapsed, still editable) sections.
  function fileEditor(entry, flagged, summaryInputs) {
    const input = flagged ? el("textarea", { rows: "2" }) : el("input", { type: "text" });
    input.value = entry.summary || "";
    summaryInputs[entry.file] = input;

    const level = confidenceLevel(entry.confidence);
    const header = el("div", { class: "file-edit-header" }, [
      el("span", { class: "file-edit-name", text: entry.file }),
      el("span", { class: `confidence ${level}`, text: entry.confidence.toFixed(2) }),
    ]);
    const children = [header, input];

    if (flagged && entry.signatures && entry.signatures.length) {
      const sigItems = entry.signatures.map(s => el("li", { text: s }));
      if (entry.signatures_more) sigItems.push(el("li", { class: "muted", text: t("pack.review.moreSignatures", { n: entry.signatures_more }) }));
      children.push(el("ul", { class: "file-list" }, sigItems));
    }
    return el("div", { class: `file-edit-row${flagged ? " needs-review" : ""}` }, children);
  }

  async function showReviewState() {
    statusBadge.className = "pack-status reviewing";
    statusBadge.textContent = t("pack.status.reviewing");

    let review;
    try {
      review = await api("/api/pack/review", { job_id: jobId });
    } catch (e) {
      return showErrorState(e.message);
    }

    // Error prevention (Nielsen heuristic #5): name/guide/rules/summary
    // edits below live only in this page's JS state until "완료 및 저장" is
    // clicked -- unlike relationship link/unlink, which hits the server
    // immediately (see add_dependency_in_job/remove_dependency_in_job).
    // A reload or window close here would silently discard all of it with
    // no warning, so guard it the same way any form with unsaved changes
    // should. Cleared on both ways out of this screen (submit succeeds,
    // cancel confirmed) so it doesn't linger and warn on an unrelated later
    // navigation.
    const beforeUnload = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);

    // The one place "discard this review" actually happens server-side --
    // shared between the Cancel button below (awaited, so it can disable
    // both buttons first and only navigate once the request settles) and
    // the topbar/sidebar/back-button navigation guard just below (fire-
    // and-forget: confirmLeaveActivePackJob() has to return synchronously,
    // see its own comment at the top of this file, so the caller can't
    // await this). Originally two separate copies of the same confirm/
    // remove-listener/cancel sequence -- factored out after code review
    // flagged them as already-diverging duplicates.
    function discardReview() {
      window.removeEventListener("beforeunload", beforeUnload);
      activeGuard = null;
      return apiPost("/api/pack/cancel", { job_id: jobId });
    }

    // Guards a topbar/sidebar/back-button navigation while this review is
    // still open -- same confirm text the Cancel button below uses, same
    // discardReview() end result, but fire-and-forget (see that function's
    // own comment for why).
    activeGuard = () => {
      if (!confirm(t("pack.review.confirmCancel"))) return false;
      discardReview().catch(() => {});
      return true;
    };

    const nameInput = el("input", { type: "text", value: review.project.name || "" });
    const promptInput = el("textarea", { rows: "3" });
    promptInput.value = review.project.prompt || "";

    let rules = [...review.rules];
    const rulesList = el("ul", { class: "rules-edit" });
    function drawRules() {
      rulesList.innerHTML = "";
      rules.forEach((rule, i) => {
        rulesList.appendChild(el("li", {}, [
          el("span", { text: rule }),
          el("button", { class: "secondary", text: t("pack.review.deleteRule"), onclick: () => { rules.splice(i, 1); drawRules(); } }),
        ]));
      });
    }
    drawRules();

    const newRuleInput = el("input", { type: "text", placeholder: t("pack.review.newRulePlaceholder") });
    const addRuleButton = el("button", { class: "secondary", text: t("pack.review.addRule"), onclick: () => {
      const rule = newRuleInput.value.trim();
      if (rule) { rules.push(rule); newRuleInput.value = ""; drawRules(); }
    } });

    const treeError = el("div", { class: "error hidden" });
    const allFileNames = [...review.needs_review, ...review.auto_kept].map(e => e.file).sort();
    const flaggedFileNames = review.needs_review.map(e => e.file);

    // Two views sharing one mutable tree: the read-only overview (default,
    // see renderDependencyTreeOverview's own comment for why) and the
    // per-file master-detail editor, swapped into the same container rather
    // than both existing at once. currentTree is the single source of truth
    // either view renders from, updated in place whenever a link/unlink
    // actually commits server-side, so switching back to the overview after
    // an edit reflects it immediately instead of the stale initial tree.
    let currentTree = review.tree;
    const relSection = el("div", {});

    function showTreeOverview() {
      relSection.innerHTML = "";
      relSection.appendChild(renderDependencyTreeOverview(currentTree, allFileNames, flaggedFileNames, showEditView));
    }

    function showEditView(selectedFile) {
      relSection.innerHTML = "";
      let relEditor;
      relEditor = renderRelationshipEditor(
        currentTree,
        allFileNames,
        async (file, target) => {
          treeError.classList.add("hidden");
          try {
            const res = await apiPost("/api/pack/link", { job_id: jobId, file, target });
            currentTree = res.tree;
            relEditor.setTree(currentTree);
          } catch (e) {
            treeError.textContent = e.message;
            treeError.classList.remove("hidden");
          }
        },
        async (file, target) => {
          treeError.classList.add("hidden");
          try {
            const res = await apiPost("/api/pack/unlink", { job_id: jobId, file, target });
            currentTree = res.tree;
            relEditor.setTree(currentTree);
          } catch (e) {
            treeError.textContent = e.message;
            treeError.classList.remove("hidden");
          }
        },
        selectedFile
      );
      relSection.appendChild(el("button", { class: "secondary", text: t("pack.review.backToTree"), onclick: showTreeOverview }));
      relSection.appendChild(relEditor.el);
    }

    showTreeOverview();

    const summaryInputs = {};
    const needsReviewBox = el("div", {}, review.needs_review.length
      ? review.needs_review.map(entry => fileEditor(entry, true, summaryInputs))
      : [el("p", { class: "muted", text: t("pack.review.noNeedsReview") })]);
    const autoKeptBox = el("div", {}, review.auto_kept.map(entry => fileEditor(entry, false, summaryInputs)));

    const submitError = el("div", { class: "error hidden" });
    const submitButton = el("button", { text: t("pack.review.submit") });
    const cancelButton = el("button", { class: "secondary", text: t("pack.review.cancel") });

    submitButton.addEventListener("click", async () => {
      submitError.classList.add("hidden");
      submitButton.disabled = true;
      cancelButton.disabled = true;
      const summaries = {};
      for (const [file, input] of Object.entries(summaryInputs)) summaries[file] = input.value.trim();
      try {
        const result = await apiPost("/api/pack/finalize", {
          job_id: jobId,
          project_name: nameInput.value.trim(),
          project_prompt: promptInput.value.trim(),
          rules,
          summaries,
        });
        window.removeEventListener("beforeunload", beforeUnload);
        body.innerHTML = "";
        showDoneState(result);
      } catch (e) {
        submitError.textContent = e.message;
        submitError.classList.remove("hidden");
        submitButton.disabled = false;
        cancelButton.disabled = false;
      }
    });

    cancelButton.addEventListener("click", async () => {
      // Error prevention: this throws away every edit made on this screen
      // (name/guide/rules/summaries -- see the beforeUnload comment above)
      // with no undo, so it gets the same one-step confirmation any
      // destructive action should have rather than firing on a single click.
      if (!confirm(t("pack.review.confirmCancel"))) return;
      // Disable both, not just this one -- otherwise a click here followed
      // fast enough by a click on "완료 및 저장" (still enabled) fires both
      // requests before either response comes back.
      cancelButton.disabled = true;
      submitButton.disabled = true;
      try { await discardReview(); } catch (e) { /* best-effort */ }
      location.hash = "#/pack"; // back to the pack-new-project screen, not the bare-logo home
    });

    body.appendChild(el("div", {}, [
      el("h3", { text: t("pack.review.projectName") }), nameInput,
      el("h3", { text: t("pack.review.aiGuide") }), promptInput,
      el("h3", { text: t("pack.review.codingRules") }), rulesList,
      el("div", { class: "toolbar" }, [newRuleInput, addRuleButton]),
      el("h3", { text: t("pack.review.fileRelations") }),
      el("p", { class: "muted", text: t("pack.review.relationsHelp") }),
      relSection, treeError,
      el("h3", { text: t("pack.review.needsReviewHeader", { n: review.needs_review.length }) }), needsReviewBox,
      el("h3", { text: t("pack.review.autoKeptHeader", { n: review.auto_kept.length }) }), autoKeptBox,
      el("div", { class: "copy-row" }, [submitButton, cancelButton]),
      submitError,
    ]));
  }

  let since = 0;
  let stopped = false;

  async function poll() {
    if (stopped) return;
    let data;
    try {
      data = await api("/api/pack/status", { job_id: jobId, since });
    } catch (e) {
      stopped = true;
      showErrorState(e.message);
      return;
    }

    if (data.log.length) {
      logPre.textContent += (logPre.textContent ? "\n" : "") + data.log.join("\n");
      logPre.scrollTop = logPre.scrollHeight;
    }
    since = data.log_len;

    // Stop controls only make sense while something's actually running to
    // stop -- request_cancel() itself already 404s past this point, but
    // hiding the buttons avoids a click that's guaranteed to fail.
    stopRow.classList.toggle("hidden", data.state !== "running");

    // "finalizing" (pack_service.py's transient state while submit_review()
    // commits) is only ever observed here if a reload/second-tab poll lands
    // in that narrow window -- keep polling through it the same as
    // "running" rather than falling into the generic error branch below.
    if (data.state === "running" || data.state === "finalizing") {
      armRunningGuard(); // see its own comment above for why this is (re)armed here, not eagerly at render time
      setTimeout(poll, 1000);
      return;
    }

    stopped = true;
    if (data.state === "reviewing") return showReviewState();
    if (data.state === "done") return showDoneState(data.result);
    return showErrorState(data.error || t("pack.unknownError"), data.retry_params);
  }
  poll();
}
