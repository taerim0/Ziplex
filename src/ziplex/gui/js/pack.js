// The pack job/review flow -- see app.js's header comment for the overall
// module split. This file: renderPackJob() -- kicks off/polls a background
// pack job (gui_server.py's /api/pack*), then the "reviewing" state's full
// correction form (project name/guide/rules/summaries, plus the
// dependency-tree overview + editor from graph.js for relationships)
// before submit/finalize.

import { app, nav, el, api, apiPost, openProject, confidenceLevel, showConfirmModal } from "./app.js";
import { t } from "./i18n.js";
import { renderDependencyTreeOverview, renderRelationshipEditor } from "./graph.js";

// Navigation guard for in-app hash links (topbar/sidebar) and the
// browser's own Back/Forward -- router.js's click-delegation and
// hashchange handler both check hasActiveGuard()/confirmLeaveActivePackJob()
// before letting a navigation through. A hash-link click (or a Back/
// Forward press) doesn't fire `beforeunload` (that only ever catches a
// real page reload/tab close), so a pack job's "running"/"reviewing"
// screen used to have no way to warn before a topbar click silently
// swapped it out with no confirmation at all -- reported directly by the
// user clicking a topbar item mid-pack and landing on a different page
// with zero warning.
//
// Leaving a "running" job now always stops it (see armRunningGuard()
// below) -- the user's own call, made explicitly after being shown that
// leaving used to let it keep computing unsupervised in the background
// with no jobs-list page to ever find it again. A "reviewing" job's
// name/guide/rules/summary edits (unlike a link/unlink, which hits the
// server immediately) still only live in that page's JS state until
// submitted, so leaving there still means discarding them.
//
// Each guard closure is `async` (showConfirmModal() below is a real DOM
// dialog awaiting a click, not window.confirm()'s blocking-but-synchronous
// native one) -- confirmLeaveActivePackJob() returns a Promise<boolean>
// accordingly; router.js's callers (a click handler, a hashchange handler)
// both cope with that asynchrony themselves rather than needing this to
// resolve before they return. hasActiveGuard() is the cheap synchronous
// half -- checked first so a click/hashchange with nothing active to guard
// never even calls into confirmLeaveActivePackJob() at all, the common
// case on every ordinary navigation.
let activeGuard = null; // null | () => Promise<boolean> (resolve false to block the navigation)

export function hasActiveGuard() {
  return activeGuard !== null;
}

// A guard invocation is now a real, non-blocking async wait on a modal --
// unlike the old window.confirm(), a second navigation attempt (a fast
// double Back-press, or a Back press landing while a click's own guard is
// still pending) can arrive before the first one's promise settles.
// pendingGuardDecision de-dupes that the same way showConfirmModal() itself
// de-dupes concurrent modal requests: a second call while one's still
// in flight gets the *same* promise instead of invoking activeGuard() (and
// its side effects -- requestStop(), discardReview()) a second time, which
// was a real bug code review caught -- two independent stop/cancel
// requests firing, and two .then() callbacks racing to set location.hash.
let pendingGuardDecision = null;

export function confirmLeaveActivePackJob() {
  if (!activeGuard) return Promise.resolve(true);
  if (pendingGuardDecision) return pendingGuardDecision;
  pendingGuardDecision = activeGuard().finally(() => { pendingGuardDecision = null; });
  return pendingGuardDecision;
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
  // Guards a topbar/sidebar click or Back/Forward press while the job is
  // still "running" -- see confirmLeaveActivePackJob()'s own comment above
  // for the full story. Leaving always stops the job now (save or
  // discard, the human's own explicit choice on this modal) rather than
  // letting it keep computing unsupervised in the background -- reusing
  // pack.stopSave/pack.stopDiscard's own labels since it's the identical
  // underlying action as the persistent Stop buttons below, just reached
  // by trying to navigate away instead of clicking one of them directly.
  // (Re)armed inside poll() itself, only once a response actually confirms
  // the job is still "running"/"finalizing" -- not set eagerly here, so a
  // click in the brief window before the first poll response lands (or a
  // reload of a job that already finished/errored server-side) never asks
  // about a job that isn't actually running anymore, a real gap code
  // review caught in an earlier version of this function.
  function armRunningGuard() {
    activeGuard = async () => {
      const choice = await showConfirmModal(t("pack.guard.runningModalMessage"), [
        { label: t("pack.stopSave"), value: "save", primary: true },
        { label: t("pack.stopDiscard"), value: "discard" },
        { label: t("pack.guard.stay"), value: null },
      ]);
      if (choice === null) return false;
      await requestStop(choice === "save");
      // Leaving is confirmed either way past this point -- clear the guard
      // so it doesn't keep firing for every later, unrelated navigation
      // (a real bug an earlier version had: declining to stop left this
      // closure armed, and the still-ticking poll() loop below re-armed it
      // every second until the job finished). stopped = true is what
      // actually silences that loop -- without it, its already-scheduled
      // setTimeout would fire ~1s later, see the job still "running"
      // server-side (the stop request above only just landed, the
      // background thread hasn't necessarily noticed it yet), and call
      // armRunningGuard() again.
      activeGuard = null;
      stopped = true;
      return true;
    };
  }

  stopSaveButton.addEventListener("click", async () => {
    if (await showConfirmModal(t("pack.confirmStopSave"), [
      { label: t("pack.guard.confirm"), value: true, primary: true },
      { label: t("pack.guard.cancel"), value: false },
    ])) requestStop(true);
  });
  stopDiscardButton.addEventListener("click", async () => {
    if (await showConfirmModal(t("pack.confirmStopDiscard"), [
      { label: t("pack.guard.confirm"), value: true, primary: true },
      { label: t("pack.guard.cancel"), value: false },
    ])) requestStop(false);
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
          // resume: true tells packager.pack() (via start_pack_job()) to
          // always resume whatever checkpoint this same job's own failure
          // just saved, regardless of retryParams.no_cache -- without it,
          // retrying a job that started with "완전히 재패킹" checked would
          // discard that checkpoint and re-bill every file's summary from
          // scratch on every retry. See pack_service.start_pack_job()'s
          // own docstring for the full story.
          const { job_id } = await apiPost("/api/pack", { ...retryParams, resume: true });
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

  // Same shape as fileEditor() above, minus confidence/signatures --
  // folder_summary.py has no per-folder confidence signal, so every folder
  // is shown for review rather than a triaged subset.
  function folderEditor(entry, folderInputs) {
    const input = el("textarea", { rows: "2" });
    input.value = entry.summary || "";
    folderInputs[entry.folder] = input;

    const display = entry.folder === "." ? t("files.rootFolder") : entry.folder;
    const header = el("div", { class: "file-edit-header" }, [
      el("span", { class: "file-edit-name", text: display }),
    ]);
    return el("div", { class: "file-edit-row" }, [header, input]);
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
    // shared between the Cancel button below and the topbar/sidebar/
    // Back-Forward navigation guard just below, both of which now show the
    // same confirmDiscardReview() modal first (originally two separate
    // copies of the same confirm/remove-listener/cancel sequence --
    // factored out after code review flagged them as already-diverging
    // duplicates).
    function discardReview() {
      window.removeEventListener("beforeunload", beforeUnload);
      activeGuard = null;
      return apiPost("/api/pack/cancel", { job_id: jobId });
    }

    // Shared confirm step for discardReview() -- reused by the Cancel
    // button below and the navigation guard just below it, so the same
    // in-page modal (not window.confirm()'s native box) backs "취소"
    // regardless of which path triggers it.
    async function confirmDiscardReview() {
      return !!(await showConfirmModal(t("pack.review.confirmCancel"), [
        { label: t("pack.guard.stay"), value: false, primary: true },
        { label: t("pack.review.cancel"), value: true },
      ]));
    }

    // Guards a topbar/sidebar/Back-Forward navigation while this review is
    // still open.
    activeGuard = async () => {
      if (!(await confirmDiscardReview())) return false;
      try { await discardReview(); } catch (e) { /* best-effort */ }
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

    const folderInputs = {};
    const foldersBox = el("div", {}, (review.folders || []).map(entry => folderEditor(entry, folderInputs)));

    const submitError = el("div", { class: "error hidden" });
    const submitButton = el("button", { text: t("pack.review.submit") });
    const cancelButton = el("button", { class: "secondary", text: t("pack.review.cancel") });

    submitButton.addEventListener("click", async () => {
      submitError.classList.add("hidden");
      submitButton.disabled = true;
      cancelButton.disabled = true;
      const summaries = {};
      for (const [file, input] of Object.entries(summaryInputs)) summaries[file] = input.value.trim();
      const folder_summaries = {};
      for (const [folder, input] of Object.entries(folderInputs)) folder_summaries[folder] = input.value.trim();
      try {
        const result = await apiPost("/api/pack/finalize", {
          job_id: jobId,
          project_name: nameInput.value.trim(),
          project_prompt: promptInput.value.trim(),
          rules,
          summaries,
          folder_summaries,
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
      if (!(await confirmDiscardReview())) return;
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
      el("h3", { text: t("pack.review.folderSummariesHeader") }), foldersBox,
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
    // Re-checked here, not just at entry: this specific request could have
    // already been in flight when the running-guard's modal resolved
    // "save & stop"/"discard & stop" (which sets stopped = true) --
    // stopping isn't instant server-side, so the response landing right
    // now can still legitimately say "running". Without this check,
    // armRunningGuard() below would re-arm activeGuard for a job the human
    // already told to stop, popping "still running, leave anyway?" again
    // on their very next click -- a real bug code review caught.
    if (stopped) return;

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
