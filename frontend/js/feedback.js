// Feedback window. Posts straight to Web3Forms from the page (the free plan only
// accepts browser submissions), which emails it to the owner.
window.DJ = window.DJ || {};

window.DJ.FEEDBACK_KINDS = [
  { key: "suggestion", label: "Suggestion", placeholder: "What would make it better?" },
  { key: "bug", label: "Bug", placeholder: "What happened, and what did you expect?" },
  { key: "other", label: "Other", placeholder: "Anything on your mind" },
];

window.DJ.feedbackMixin = {
  showFeedback: false,
  fb: { kind: "suggestion", message: "", email: "" },
  fbState: "idle",   // idle | sending | sent | error
  fbError: "",
  fbErrorField: "",

  openFeedback(kind) {
    this.fb = { kind: kind || "suggestion", message: "", email: "" };
    this.fbState = "idle";
    this.fbError = "";
    this.fbErrorField = "";
    this.showAbout = false;
    this.showFeedback = true;
    this.$nextTick(() => document.getElementById("fb-message")?.focus());
  },

  fbPlaceholder() {
    return (window.DJ.FEEDBACK_KINDS.find(k => k.key === this.fb.kind) || {}).placeholder || "";
  },

  async sendFeedback() {
    const message = this.fb.message.trim();
    if (!message) { this.fbState = "error"; this.fbErrorField = "message"; this.fbError = "Write a message first."; return; }
    const kind = (window.DJ.FEEDBACK_KINDS.find(k => k.key === this.fb.kind) || {}).label || "Other";
    const plat = this.meta.platform || "unknown";
    const payload = {
      access_key: this.meta.feedback_key,
      subject: `DJ Track Organizer: ${kind} (v${this.meta.version || "?"})`,
      from_name: "DJ Track Organizer",
      type: kind,
      message,
      version: this.meta.version || "",
      platform: plat,
    };
    const email = this.fb.email.trim();
    if (email) { payload.email = email; payload.replyto = email; }
    this.fbState = "sending";
    this.fbError = "";
    this.fbErrorField = "";
    DJ.anim.fx(document.querySelector(".feedback-modal .foot .ic-envelope-simple"), "fx-fly");
    try {
      const res = await fetch("https://api.web3forms.com/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) throw new Error(data.message || `HTTP ${res.status}`);
      this.fbState = "sent";
      this.dismissFeedbackNudge();
      setTimeout(() => { if (this.fbState === "sent") this.showFeedback = false; }, 1900);
    } catch (e) {
      this.fbState = "error";
      this.fbError = e instanceof TypeError
        ? "Couldn't reach the feedback service. Check your connection and try again."
        : "Not sent: " + e.message;
    }
  },

  // One-time banner, shown once the app has actually been used.
  feedbackNudge() {
    return !!this.meta.feedback_key
      && this.settings.feedback_nudge !== "done"
      && !!this.stats.last_update
      && (this.stats.listened || 0) >= 25;
  },

  dismissFeedbackNudge() {
    if (this.settings.feedback_nudge === "done") return;
    this.settings.feedback_nudge = "done";
    api.patchSettings({ feedback_nudge: "done" }).catch(() => {});
  },
};
