function updateCoopPairingVisibility() {
  const mode = document.getElementById("coopAssignmentMode");
  const pairing = document.getElementById("coopPairing");
  if (mode && pairing) pairing.disabled = mode.value === "random";
}

async function loadCoopSettings() {
  const mode = document.getElementById("coopAssignmentMode");
  if (!mode) return;
  try {
    const response = await fetchShared("/coop_settings", { cache: "no-store" });
    const data = await response.json();
    mode.value = data.settings?.assignment_mode || "specified";
    const pairing = document.getElementById("coopPairing");
    if (pairing) pairing.value = data.settings?.pairing || "12_34";
    updateCoopPairingVisibility();
  } catch (error) {
    const status = document.getElementById("coopSettingsStatus");
    if (status) status.innerText = "協力モード設定の取得に失敗しました";
  }
}

async function selectCoopGame() {
  const status = document.getElementById("coopSettingsStatus");
  try {
    const response = await fetchShared("/select_coop_mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        assignment_mode: document.getElementById("coopAssignmentMode")?.value || "specified",
        pairing: document.getElementById("coopPairing")?.value || "12_34"
      })
    });
    const data = await response.json();
    if (!response.ok) {
      if (status) status.innerText = data.message || "協力モードの選択に失敗しました";
      return;
    }
    const current = document.getElementById("mode-current");
    if (current) current.innerText = "現在の設定：2対2協力モード";
    if (status) status.innerText = "2対2協力モードを選択しました。セット数を決めて開始してください。";
  } catch (error) {
    if (status) status.innerText = "協力モード選択の通信エラー";
  }
}

async function refreshCoopModeLabel() {
  const current = document.getElementById("mode-current");
  if (!current) return;
  try {
    const response = await fetchShared("/get_control_mode", { cache: "no-store" });
    const data = await response.json();
    if (data.mode === "team_coop") current.innerText = "現在の設定：2対2協力モード";
  } catch (_) {
    // 通常画面の状態更新を優先し、補助表示の失敗は無視する。
  }
}

window.addEventListener("DOMContentLoaded", () => {
  loadCoopSettings();
  refreshCoopModeLabel();
  pollWithoutOverlap(refreshCoopModeLabel, 2000);
});
